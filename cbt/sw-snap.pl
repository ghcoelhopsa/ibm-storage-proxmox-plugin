#!/usr/bin/perl
# Helper for the CBT backup from the array snapshot (proof of concept).
# Reuses the functions of the StorwizePlugin plugin.
#
#   sw-snap.pl prepare <storeid> <vmid> <snap> <volname>...   create targets+maps+CG and prestart (flush)
#   sw-snap.pl trigger <storeid> <vmid> <snap>                startfcconsistgrp (fast, no -prep)
#   sw-snap.pl map     <storeid> <volname> <snap>             map the snapshot target to the host and print the device
#   sw-snap.pl unmap   <storeid> <volname> <snap>             remove the local device and unmap
#   sw-snap.pl delete  <storeid> <volname> <snap>             delete the snapshot (plugin)
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
    $P->can('device_remove_local')->($info->{vdisk_UID});
    for my $m (@{ $P->can('sw_list')->($scfg, $storeid, 'lsvdiskhostmap', [], $tgt) }) {
        $run->('rmvdiskhostmap', [host => $m->{host_name}], $tgt);
    }
    print "unmapped $tgt\n";
} elsif ($cmd eq 'delete') {
    my ($v, $snap) = @a;
    $P->volume_snapshot_delete($scfg, $storeid, $v, $snap);
    print "deleted $v\@$snap\n";
} else {
    die "unknown command: $cmd\n";
}
