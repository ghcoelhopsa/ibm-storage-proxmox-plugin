#!/usr/bin/perl
# Helper for the CBT backup from the array snapshot (proof of concept).
# Reuses the functions of the StorwizePlugin plugin.
#
#   sw-snap.pl prepare <storeid> <vmid> <snap> <volname>...   create targets+maps+CG and prestart (flush)
#   sw-snap.pl trigger <storeid> <vmid> <snap>                startfcconsistgrp (fast, no -prep)
#   sw-snap.pl map     <storeid> <volname> <snap>             map the snapshot target to the host and print the device
#   sw-snap.pl unmap   <storeid> <volname> <snap>             remove the local device and unmap
#   sw-snap.pl delete  <storeid> <volname> <snap>             delete the snapshot (plugin)
#
# Volume group mode (sw-snapmode=volumegroup, Storage Virtualize 8.5+/9.x):
#   sw-snap.pl latency  <storeid>                             array volume latency (ms): overall read write
#   sw-snap.pl vgsnap   <storeid> <vmid> <snap>               addsnapshot of the guest volume group
#   sw-snap.pl vgmap    <storeid> <vmid> <snap> <volname>...  thinclone of the snapshot, mapped to this
#                                                             node only; prints "<volname> <device>"
#   sw-snap.pl vgclean  <storeid> <vmid> <snap>               remove the thinclones and the snapshot
#
# File restore (any snapshot of the guest's volume group, including Safeguarded ones):
#   sw-snap.pl fsmap    <storeid> <vmid> <snap>               thinclone of every volume of the
#                                                             snapshot, mapped to this node only;
#                                                             prints "group <name>", then
#                                                             "<source volume> <device>" per volume
#   sw-snap.pl fsclean  <storeid> <group>                     remove such a thinclone group
use strict;
use warnings;
use Time::HiRes qw(time);
use PVE::Storage;
use PVE::Storage::Custom::StorwizePlugin;

my $P = 'PVE::Storage::Custom::StorwizePlugin';
my ($cmd, $storeid, @a) = @ARGV;
die "usage: $0 <cmd> <storeid> ...\n" if !$cmd || !$storeid;
my $scfg = PVE::Storage::config()->{ids}->{$storeid} or die "storage $storeid does not exist\n";
my $run = sub { $P->can('sw_run')->($scfg, $storeid, @_) };
my $t0 = time();

if ($cmd eq 'prepare') {
    my ($vmid, $snap, @vols) = @a;
    my $cg = $P->can('cg_name')->($scfg, $vmid, $snap);
    $run->('mkfcconsistgrp', [name => $cg]);
    for my $v (@vols) {
        my $src = $P->can('vdisk_name')->($scfg, $v);
        my $tgt = $P->can('snap_vdisk_name')->($scfg, $v, $snap);
        my $info = $P->can('vdisk_info')->($scfg, $storeid, $src) or die "volume $src does not exist\n";
        $run->('mkvdisk', [name => $tgt, mdiskgrp => $scfg->{'sw-snappool'} // $scfg->{'sw-pool'},
            iogrp => $info->{IO_group_name}, size => int($info->{capacity}), unit => 'b',
            rsize => '0%', autoexpand => undef]);
        $run->('mkfcmap', [source => $src, target => $tgt, copyrate => 0, cleanrate => 50, consistgrp => $cg]);
    }
    $run->('prestartfcconsistgrp', [], $cg);
    # wait for the 'prepared' state (array cache flushed)
    for (1 .. 120) {
        my $g = $P->can('sw_show')->($scfg, $storeid, 'lsfcconsistgrp', [], $cg);
        last if ($g->{status} // '') eq 'prepared';
        die "CG $cg in state '$g->{status}'\n" if ($g->{status} // '') =~ m/^(stopped|idle_or_copied)$/;
        select(undef, undef, undef, 0.25);
    }
    printf "prepared %s %.2fs\n", $cg, time() - $t0;
} elsif ($cmd eq 'trigger') {
    my ($vmid, $snap) = @a;
    my $cg = $P->can('cg_name')->($scfg, $vmid, $snap);
    $run->('startfcconsistgrp', [], $cg);
    printf "started %s %.3fs\n", $cg, time() - $t0;
} elsif ($cmd eq 'map') {
    my ($v, $snap) = @a;
    my $tgt = $P->can('snap_vdisk_name')->($scfg, $v, $snap);
    my $host = (split(/,/, $scfg->{'sw-hosts'} // ''))[0] or die "sw-hosts is empty\n";
    $run->('mkvdiskhostmap', [force => undef, host => $host], $tgt);
    my $uid = $P->can('vdisk_info')->($scfg, $storeid, $tgt)->{vdisk_UID};
    my $dev = $P->can('uid_to_path')->($uid);
    my $wwid = $P->can('uid_to_wwid')->($uid);
    for my $i (1 .. 40) {
        last if -b $dev;
        $P->can('rescan_hosts')->($scfg) if $i % 5 == 1;
        $P->can('multipath_add')->($wwid);
        sleep(1);
    }
    die "device $dev did not show up\n" if !-b $dev;
    print "$dev\n";
} elsif ($cmd eq 'unmap') {
    my ($v, $snap) = @a;
    my $tgt = $P->can('snap_vdisk_name')->($scfg, $v, $snap);
    my $info = $P->can('vdisk_info')->($scfg, $storeid, $tgt) or exit 0;
    $P->can('device_remove')->($info->{vdisk_UID});
    for my $m (@{ $P->can('sw_list')->($scfg, $storeid, 'lsvdiskhostmap', [], $tgt) }) {
        $run->('rmvdiskhostmap', [host => $m->{host_name}], $tgt);
    }
    print "unmapped $tgt\n";
} elsif ($cmd eq 'delete') {
    my ($v, $snap) = @a;
    $P->volume_snapshot_delete($scfg, $storeid, $v, $snap);
    print "deleted $v\@$snap\n";
} elsif ($cmd eq 'latency') {
    # volume latency of the array in ms: overall, read, write (lssystemstats, current values)
    my %v = map { ($_->{stat_name} // '') => $_->{stat_current} }
        @{ $P->can('sw_list')->($scfg, $storeid, 'lssystemstats', []) };
    print join(' ', map { $v{$_} // 0 } qw(vdisk_ms vdisk_r_ms vdisk_w_ms)), "\n";
} elsif ($cmd eq 'vgsnap') {
    my ($vmid, $snap) = @a;
    my $vg = $P->can('guest_vg_name')->($scfg, $vmid);
    $run->('addsnapshot', [volumegroup => $vg, name => $snap]);
    printf "snapshot %s %.3fs\n", $snap, time() - $t0;
} elsif ($cmd eq 'vgmap') {
    my ($vmid, $snap, @vols) = @a;
    my $vg = $P->can('guest_vg_name')->($scfg, $vmid);
    my $ro = ro_group($vmid, $snap);
    my $host = local_host();
    $run->('mkvolumegroup', [type => 'thinclone', snapshot => $snap, fromsourcegroup => $vg,
        fromsourcevolumes => join(':', map { $P->can('vdisk_name')->($scfg, $_) } @vols),
        name => $ro, pool => $scfg->{'sw-pool'}]);
    my %by_src;
    my $n = 0;
    for my $r (@{ $P->can('sw_list')->($scfg, $storeid, 'lsvdisk', [filtervalue => "volume_group_name=$ro"]) }) {
        my $d = $P->can('sw_show')->($scfg, $storeid, 'lsvdisk', [], $r->{id});
        # out of the <prefix>-vm-* namespace, so Proxmox never lists it as a guest disk
        $run->('chvdisk', [name => "$ro-" . $n++], $r->{id});
        $d->{name} = "$ro-" . ($n - 1);
        $by_src{$d->{source_volume_name} // ''} = $d;
    }
    for my $v (@vols) {
        my $d = $by_src{ $P->can('vdisk_name')->($scfg, $v) } or die "no thinclone for $v in $ro\n";
        $run->('mkvdiskhostmap', [force => undef, host => $host], $d->{name});
    }
    for my $v (@vols) {
        my $uid = $by_src{ $P->can('vdisk_name')->($scfg, $v) }->{vdisk_UID};
        my $dev = $P->can('uid_to_path')->($uid);
        my $wwid = $P->can('uid_to_wwid')->($uid);
        for my $i (1 .. 40) {
            last if -b $dev;
            $P->can('rescan_hosts')->($scfg) if $i % 5 == 1;
            $P->can('multipath_add')->($wwid);
            sleep(1);
        }
        die "device $dev did not show up\n" if !-b $dev;
        print "$v $dev\n";
    }
} elsif ($cmd eq 'vgclean') {
    my ($vmid, $snap) = @a;
    my $vg = $P->can('guest_vg_name')->($scfg, $vmid);
    my $ro = ro_group($vmid, $snap);
    if ($P->can('sw_show_opt')->($scfg, $storeid, 'lsvolumegroup', [], $ro)) {
        for my $r (@{ $P->can('sw_list')->($scfg, $storeid, 'lsvdisk', [filtervalue => "volume_group_name=$ro"]) }) {
            eval { $P->can('device_remove')->($r->{vdisk_UID}) } if $r->{vdisk_UID};
            $run->('rmvdisk', [removehostmappings => undef], $r->{name});
        }
        $run->('rmvolumegroup', [], $ro);
    }
    $run->('rmsnapshot', [snapshot => $snap, volumegroup => $vg])
        if $P->can('vg_snapshot_exists')->($scfg, $storeid, $vg, $snap);
    print "cleaned $snap\n";
} elsif ($cmd eq 'fsmap') {
    my ($vmid, $snap) = @a;
    die "invalid VM id\n" if ($vmid // '') !~ m/^\d+$/;
    die "invalid snapshot name\n" if ($snap // '') !~ m/^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/;
    my $vg = $P->can('guest_vg_name')->($scfg, $vmid);
    die "snapshot '$snap' not found in volume group '$vg'\n"
        if !$P->can('vg_snapshot_exists')->($scfg, $storeid, $vg, $snap);
    require Digest::MD5;
    my $t = int(time());
    # same naming as the plugin's snapshot access groups: the hourly cleanup removes leftovers
    my $ro = $P->can('sw_prefix')->($scfg) . "-snapro-$vmid-$t-" . substr(Digest::MD5::md5_hex("files$snap$$"), 0, 6);
    my $host = local_host();
    $run->('mkvolumegroup', [type => 'thinclone', snapshot => $snap, fromsourcegroup => $vg,
        name => $ro, pool => $scfg->{'sw-pool'}]);
    my @out;
    eval {
        my $n = 0;
        for my $r (@{ $P->can('sw_list')->($scfg, $storeid, 'lsvdisk', [filtervalue => "volume_group_name=$ro"]) }) {
            my $d = $P->can('sw_show')->($scfg, $storeid, 'lsvdisk', [], $r->{id});
            my $name = "$ro-" . $n++;
            $run->('chvdisk', [name => $name], $r->{id});
            $run->('mkvdiskhostmap', [force => undef, host => $host], $name);
            push @out, [$d->{source_volume_name} // $d->{name}, $d->{vdisk_UID}];
        }
        die "the snapshot has no volume\n" if !@out;
        for my $o (@out) {
            my $dev = $P->can('uid_to_path')->($o->[1]);
            my $wwid = $P->can('uid_to_wwid')->($o->[1]);
            for my $i (1 .. 40) {
                last if -b $dev;
                $P->can('rescan_hosts')->($scfg) if $i % 5 == 1;
                $P->can('multipath_add')->($wwid);
                sleep(1);
            }
            die "device $dev did not show up\n" if !-b $dev;
            $o->[2] = $dev;
        }
    };
    if (my $e = $@) {
        eval { fs_clean($ro) };
        die $e;
    }
    print "group $ro\n";
    print "$_->[0] $_->[2]\n" for @out;
} elsif ($cmd eq 'fsclean') {
    my ($g) = @a;
    my $p = quotemeta($P->can('sw_prefix')->($scfg));
    die "not a snapshot access group: " . ($g // '') . "\n" if ($g // '') !~ m/^${p}-snapro-\d+-\d+-[0-9a-f]{6}$/;
    fs_clean($g);
    print "cleaned $g\n";
} else {
    die "unknown command: $cmd\n";
}

# Removes a thinclone group made for reading a snapshot: local devices, mappings, volumes, group.
sub fs_clean {
    my ($g) = @_;
    return if !$P->can('sw_show_opt')->($scfg, $storeid, 'lsvolumegroup', [], $g);
    for my $r (@{ $P->can('sw_list')->($scfg, $storeid, 'lsvdisk', [filtervalue => "volume_group_name=$g"]) }) {
        eval { $P->can('device_remove')->($r->{vdisk_UID}) } if $r->{vdisk_UID};
        $run->('rmvdisk', [removehostmappings => undef], $r->{name});
    }
    $run->('rmvolumegroup', [], $g);
}

# Thinclone group read by the backup. The name follows the plugin's snapshot access groups
# (<prefix>-snapro-<vmid>-<epoch>-<6 hex>), so the hourly cleanup removes leftovers after a day.
sub ro_group {
    my ($vmid, $snap) = @_;
    require Digest::MD5;
    my ($t) = $snap =~ m/(\d{9,11})$/ or die "snapshot name without a timestamp: $snap\n";
    return $P->can('sw_prefix')->($scfg) . "-snapro-$vmid-$t-" . substr(Digest::MD5::md5_hex("cbt$snap"), 0, 6);
}

# Host object of this node on the array: the one whose FC ports are logged in from this node's
# HBAs, or whose iSCSI name is this node's IQN. Only hosts the storage maps to are considered.
sub local_host {
    my @cand;
    if (my $hc = $scfg->{'sw-hostcluster'}) {
        @cand = map { $_->{name} } @{ $P->can('sw_list')->($scfg, $storeid, 'lshost', [filtervalue => "host_cluster_name=$hc"]) };
    } else {
        @cand = grep { length } split(/\s*,\s*/, $scfg->{'sw-hosts'} // '');
    }
    my %wwpn;
    for my $h (glob('/sys/class/fc_host/host*')) {
        open(my $fh, '<', "$h/port_name") or next;
        my $w = <$fh>; close($fh);
        $w =~ s/^0x//; chomp $w;
        $wwpn{uc $w} = 1;
    }
    my $iqn = '';
    if (open(my $fh, '<', '/etc/iscsi/initiatorname.iscsi')) {
        while (<$fh>) { $iqn = $1 if m/^InitiatorName=(\S+)/ }
        close($fh);
    }
    for my $h (@cand) {
        my $logins = eval { $P->can('sw_list')->($scfg, $storeid, 'lsfabric', [host => $h]) } // [];
        return $h if grep { $wwpn{ uc($_->{remote_wwpn} // '') } } @$logins;
        if ($iqn) {
            my $d = eval { $P->can('sw_show')->($scfg, $storeid, 'lshost', [], $h) } // {};
            return $h if ($d->{iscsi_name} // '') eq $iqn;
        }
    }
    die "this node is not one of the hosts of storage $storeid on the array\n";
}
