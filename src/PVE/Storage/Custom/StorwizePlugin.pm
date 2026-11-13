package PVE::Storage::Custom::StorwizePlugin;

# Proxmox VE storage plugin for the IBM Storage Virtualize family
# (SAN Volume Controller, Storwize V7000/V5000, FlashSystem).
#
# Model: 1 volume (vdisk) on the array = 1 virtual disk of a VM, raw, no LVM
# on the host. Data path FC/iSCSI + dm-multipath, management path through
# one of two backends sharing the same command layer:
#   - ssh : CLI over SSH with a key (svcinfo/svctask), works on 7.x and 8.x
#   - rest: REST API on port 7443 (8.x), JWT token
#
# Snapshots: FlashCopy (every code level).
#
# License: AGPL-3.0-or-later

use strict;
use warnings;

use Cwd qw(abs_path);
use File::Path qw(make_path);
use JSON;

use PVE::Tools qw(run_command file_read_firstline file_set_contents);
use PVE::Storage::Plugin;

use base qw(PVE::Storage::Plugin);

our $VERSION = '0.3.4';

# Storage API this module was validated against (PVE 9.2, libpve-storage-perl 9.1).
my $TESTED_APIVER = 14;

my $PRIV_DIR  = '/etc/pve/priv/storwize';
my $RUN_DIR   = '/run/pve-ibm-storage';
my $KNOWN_HOSTS = "$PRIV_DIR/known_hosts";

my $STATUS_CACHE_TTL = 30;      # pvestatd polls every 10 s on every node
my $SYSTEM_CACHE_TTL = 3600;
my $DEV_WAIT         = 30;      # seconds to wait for a new multipath device
my $FC_STOP_WAIT     = 900;     # seconds to wait for a FlashCopy map to stop

my $ARG_RE      = qr/^[A-Za-z0-9_.:=*%\/-]+$/;
my $VOLNAME_RE  = qr/^vm-(\d+)-[A-Za-z0-9][A-Za-z0-9_-]*$/;
my $NOTFOUND_RE = qr/CMMVC5753E|CMMVC5804E|does not exist/i;
my $MAX_NAME    = 63;

push @PVE::Storage::Plugin::SHARED_STORAGE, 'storwize'
    if !grep { $_ eq 'storwize' } @PVE::Storage::Plugin::SHARED_STORAGE;

# ---------------------------------------------------------------------------
# Plugin definition
# ---------------------------------------------------------------------------

sub api {
    my $apiver = eval { PVE::Storage::APIVER() } // $TESTED_APIVER;
    my $apiage = eval { PVE::Storage::APIAGE() } // 0;
    # Claim the installed API when it is still compatible with what we tested,
    # so PVE does not warn on every load; otherwise claim what we tested.
    return $apiver if ($apiver - $apiage) <= $TESTED_APIVER;
    return $TESTED_APIVER;
}

sub type { return 'storwize'; }

sub plugindata {
    return {
        content => [{ images => 1, rootdir => 1 }, { images => 1 }],
        format => [{ raw => 1 }, 'raw'],
        'sensitive-properties' => { password => 1, 'sw-sshprivkey' => 1, 'sw-sshhostkey' => 1 },
    };
}

sub properties {
    return {
        'sw-address' => {
            description => "Management IP/hostname of the IBM Storage Virtualize system.",
            type => 'string', format => 'address',
        },
        'sw-api' => {
            description => "Management backend: ssh (CLI, any code level), rest (8.x, port 7443)"
                . " or auto (rest if a password is stored, else ssh).",
            type => 'string', enum => ['auto', 'ssh', 'rest'], default => 'auto',
        },
        'sw-user' => {
            description => "User on the storage system (Administrator group).",
            type => 'string', pattern => '[A-Za-z0-9_.@-]+',
        },
        'sw-sshkey' => {
            description => "SSH private key (default $PRIV_DIR/<storeid>_id_rsa).",
            type => 'string', format => 'pve-storage-path',
        },
        'sw-sshprivkey' => {
            description => "SSH private key (base64 of the PEM file). Written to"
                . " $PRIV_DIR/<storeid>_id_rsa - never stored in storage.cfg.",
            type => 'string', pattern => '[A-Za-z0-9+/=]+', maxLength => 16384,
        },
        'sw-sshhostkey' => {
            description => "Array SSH host key(s) (base64 of known_hosts lines), added to $KNOWN_HOSTS.",
            type => 'string', pattern => '[A-Za-z0-9+/=]+', maxLength => 16384,
        },
        'sw-port' => {
            description => "Management port (default 22 for ssh, 7443 for rest).",
            type => 'integer', minimum => 1, maximum => 65535,
        },
        'sw-fingerprint' => {
            description => "SHA-256 fingerprint of the REST certificate (pinned automatically on add).",
            type => 'string', pattern => '([A-Fa-f0-9]{2}:){31}[A-Fa-f0-9]{2}',
        },
        'sw-pool' => {
            description => "Pool or child pool (mdiskgrp) where volumes are created.",
            type => 'string', pattern => '[A-Za-z0-9_.-]+',
        },
        'sw-snappool' => {
            description => "Pool for FlashCopy snapshot targets (default: sw-pool).",
            type => 'string', pattern => '[A-Za-z0-9_.-]+',
        },
        'sw-hostcluster' => {
            description => "Host cluster that receives every volume mapping.",
            type => 'string', pattern => '[A-Za-z0-9_.-]+',
        },
        'sw-hosts' => {
            description => "Comma separated host objects (used when sw-hostcluster is not set).",
            type => 'string', pattern => '[A-Za-z0-9_.-]+(,[A-Za-z0-9_.-]+)*',
        },
        'sw-prefix' => {
            description => "Name prefix of every volume managed by this storage.",
            type => 'string', pattern => '[A-Za-z][A-Za-z0-9_-]{0,15}', default => 'pve',
        },
        'sw-transport' => {
            description => "Data path used for rescans.",
            type => 'string', enum => ['auto', 'fc', 'iscsi', 'nvme-tcp'], default => 'auto',
        },
        'sw-portals' => {
            description => "NVMe over TCP portals of the array (IP[:port], comma separated; default port 4420)."
                . " Used with sw-transport=nvme-tcp. EXPERIMENTAL.",
            type => 'string', pattern => '[0-9A-Fa-f:.,\\[\\]]+',
        },
        'sw-iogrp' => {
            description => "I/O group of new volumes.",
            type => 'string', pattern => '[A-Za-z0-9_.-]+', default => 'io_grp0',
        },
        'sw-thin' => {
            description => "Create thin-provisioned volumes.",
            type => 'boolean', default => 1,
        },
        'sw-volumegroup' => {
            description => "Volume group that new volumes join (8.3+; e.g. for policy-based replication).",
            type => 'string', pattern => '[A-Za-z0-9_.-]+',
        },
        'sw-partition' => {
            description => "Storage partition (8.6.1+ / 9.x) that owns the volume groups, volumes and hosts"
                . " of this storage. Needs sw-snapmode=volumegroup. EXPERIMENTAL.",
            type => 'string', pattern => '[A-Za-z0-9_.-]+',
        },
        'sw-consistency' => {
            description => "Snapshot all disks of a guest on this storage at the same instant"
                . " (FlashCopy consistency group).",
            type => 'boolean', default => 1,
        },
        'sw-trash-days' => {
            description => "Days a removed disk stays in the trash on the array (unmapped and renamed"
                . " <prefix>-trash<time>-<volume>) before it is deleted. 0 deletes at once.",
            type => 'integer', minimum => 0, maximum => 3650, default => 7,
        },
        'sw-snapmode' => {
            description => "Snapshot engine: flashcopy (every code level) or volumegroup"
                . " (8.5.1+, one volume group per guest + addsnapshot; EXPERIMENTAL).",
            type => 'string', enum => ['flashcopy', 'volumegroup'], default => 'flashcopy',
        },
    };
}

sub options {
    return {
        'sw-address'     => { fixed => 1 },
        'sw-pool'        => { fixed => 1 },
        'sw-prefix'      => { fixed => 1, optional => 1 },
        'sw-api'         => { optional => 1 },
        'sw-user'        => {},
        'sw-sshkey'      => { optional => 1 },
        'sw-port'        => { optional => 1 },
        'sw-fingerprint' => { optional => 1 },
        password         => { optional => 1 },
        'sw-sshprivkey'  => { optional => 1 },
        'sw-sshhostkey'  => { optional => 1 },
        'sw-snappool'    => { optional => 1 },
        'sw-hostcluster' => { optional => 1 },
        'sw-hosts'       => { optional => 1 },
        'sw-transport'   => { optional => 1 },
        'sw-portals'     => { optional => 1 },
        'sw-iogrp'       => { optional => 1 },
        'sw-thin'        => { optional => 1 },
        'sw-volumegroup' => { optional => 1 },
        'sw-consistency' => { optional => 1 },
        'sw-trash-days'  => { optional => 1 },
        'sw-snapmode'    => { optional => 1, fixed => 1 },
        'sw-partition'   => { optional => 1, fixed => 1 },
        nodes            => { optional => 1 },
        disable          => { optional => 1 },
        content          => { optional => 1 },
        shared           => { optional => 1 },
    };
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

sub sw_prefix { my ($scfg) = @_; return $scfg->{'sw-prefix'} // 'pve'; }

# ---------------------------------------------------------------------------
# Trash: a removed disk is unmapped and renamed <prefix>-trash<epoch>-<volname> instead of being
# deleted. The name keeps everything needed to restore it; nothing else is stored. The status
# poll deletes the ones older than sw-trash-days.
sub trash_days {
    my ($scfg) = @_;
    my ($d) = ($scfg->{'sw-trash-days'} // 7) =~ m/^(\d+)$/;
    return $d // 7;
}

sub trash_name {
    my ($scfg, $volname, $time) = @_;
    my $n = sw_prefix($scfg) . '-trash' . int($time) . "-$volname";
    return length($n) <= $MAX_NAME ? $n : undef;
}

sub trash_parse {
    my ($scfg, $name) = @_;
    my $p = quotemeta(sw_prefix($scfg));
    my ($t, $volname) = ($name // '') =~ m/^${p}-trash(\d{9,11})-(vm-\d+-[A-Za-z0-9][A-Za-z0-9_-]*)$/
        or return undef;
    my ($owner) = $volname =~ $VOLNAME_RE;
    return { name => "$name", deleted => int($t), volname => $volname, vmid => int($owner) };
}

sub trash_list {
    my ($scfg, $storeid) = @_;
    my $p = sw_prefix($scfg);
    my $rows = sw_list($scfg, $storeid, 'lsvdisk', [bytes => undef, filtervalue => "name=$p-trash*"]);
    my $keep = trash_days($scfg) * 86400;
    my @res;
    for my $r (@$rows) {
        my $t = trash_parse($scfg, $r->{name}) or next;
        $t->{size} = int($r->{capacity} // 0);
        $t->{uid} = $r->{vdisk_UID};
        $t->{expires} = $t->{deleted} + $keep;
        push @res, $t;
    }
    return [sort { $a->{deleted} <=> $b->{deleted} } @res];
}

sub trash_delete {
    my ($scfg, $storeid, $name) = @_;
    my $t = trash_parse($scfg, $name)
        or die "storwize: '$name' is not a volume in the trash of this storage\n";
    sw_run($scfg, $storeid, 'rmvdisk', [], $t->{name});
    unlink run_dir($storeid) . "/status.json";
    return 1;
}

# deletes what is past the retention; a volume someone mapped again by hand is refused by
# rmvdisk and stays
sub trash_purge {
    my ($scfg, $storeid) = @_;
    my @done;
    my $now = time();
    for my $t (@{ trash_list($scfg, $storeid) }) {
        next if $now < $t->{expires};
        eval { trash_delete($scfg, $storeid, $t->{name}) };
        if (my $err = $@) {
            warn "storwize: trash: '$t->{name}' kept: $err";
            next;
        }
        push @done, $t->{name};
    }
    return \@done;
}

# back under its own name (or the next free disk name of $vmid) and mapped to the hosts again
sub trash_restore {
    my ($class, $scfg, $storeid, $name, $vmid) = @_;
    my $t = trash_parse($scfg, $name)
        or die "storwize: '$name' is not a volume in the trash of this storage\n";
    vdisk_info($scfg, $storeid, $t->{name}) or die "storwize: '$t->{name}' is not on the array\n";
    $vmid //= $t->{vmid};
    ($vmid) = "$vmid" =~ m/^(\d{1,9})$/ or die "storwize: invalid VMID\n";

    my $target = $t->{volname};
    if ($vmid != $t->{vmid} || vdisk_info($scfg, $storeid, vdisk_name($scfg, $target))) {
        my %used = map { (split(/:/, $_->{volid}, 2))[1] => 1 }
            @{ $class->list_images($storeid, $scfg, $vmid) };
        $target = undef;
        for my $i (0 .. 999) {
            next if $used{"vm-$vmid-disk-$i"};
            $target = "vm-$vmid-disk-$i";
            last;
        }
        die "storwize: no free disk name for VM $vmid\n" if !defined($target);
    }
    my $dst = vdisk_name($scfg, $target);
    sw_run($scfg, $storeid, 'chvdisk', [name => $dst], $t->{name});
    map_vdisk($scfg, $storeid, $dst);
    join_guest_vg($scfg, $storeid, $vmid, $dst)
        if snapmode($scfg) eq 'volumegroup' && !$scfg->{'sw-volumegroup'} && !$scfg->{'sw-partition'};
    unlink uid_cache_file($storeid, $target);
    unlink run_dir($storeid) . "/status.json";
    return "$storeid:$target";
}

sub run_dir {
    my ($storeid) = @_;
    my ($id) = ($storeid // '') =~ m/^([A-Za-z][A-Za-z0-9_.-]*)$/
        or die "invalid storage id '" . ($storeid // '') . "'\n";
    my $dir = "$RUN_DIR/$id";
    make_path($dir, { mode => 0700 }) if !-d $dir;
    return $dir;
}

sub password_file { my ($storeid) = @_; return "$PRIV_DIR/$storeid.pw"; }

sub get_password {
    my ($storeid) = @_;
    my $f = password_file($storeid);
    return undef if !-f $f;
    return file_read_firstline($f);
}

sub set_password {
    my ($storeid, $pw) = @_;
    make_path($PRIV_DIR, { mode => 0700 }) if !-d $PRIV_DIR;
    file_set_contents(password_file($storeid), "$pw\n", 0600);
}

sub backend {
    my ($scfg, $storeid) = @_;
    my $api = $scfg->{'sw-api'} // 'auto';
    return $api if $api ne 'auto';
    return defined(get_password($storeid)) ? 'rest' : 'ssh';
}

sub check_arg {
    my ($v) = @_;
    die "storwize: refusing unsafe CLI argument '" . ($v // 'undef') . "'\n"
        if !defined($v) || $v !~ $ARG_RE;
    ($v) = $v =~ m/^(.*)$/s;    # untaint (pvedaemon runs with -T)
    return $v;
}

sub vdisk_name {
    my ($scfg, $volname) = @_;
    die "storwize: invalid volume name '$volname'\n" if $volname !~ $VOLNAME_RE;
    my $n = sw_prefix($scfg) . "-$volname";
    die "storwize: volume name '$n' longer than $MAX_NAME characters\n" if length($n) > $MAX_NAME;
    return $n;
}

sub snap_vdisk_name {
    my ($scfg, $volname, $snap) = @_;
    die "storwize: invalid snapshot name '$snap'\n" if $snap !~ m/^[A-Za-z][A-Za-z0-9_-]*$/;
    my $n = vdisk_name($scfg, $volname) . "_s_$snap";
    die "storwize: snapshot volume name '$n' longer than $MAX_NAME characters"
        . " - use a shorter snapshot name\n" if length($n) > $MAX_NAME;
    return $n;
}

sub is_own_snap_target {
    my ($scfg, $name) = @_;
    my $p = quotemeta(sw_prefix($scfg));
    return $name =~ m/^${p}-vm-\d+-[A-Za-z0-9][A-Za-z0-9_-]*_s_[A-Za-z][A-Za-z0-9_-]*$/;
}

sub mapping_targets {
    my ($scfg) = @_;
    if (my $hc = $scfg->{'sw-hostcluster'}) {
        return { hostcluster => $hc };
    }
    my @hosts = grep { length } split(/\s*,\s*/, $scfg->{'sw-hosts'} // '');
    die "storwize: configure sw-hostcluster or sw-hosts\n" if !@hosts;
    return { hosts => \@hosts };
}

sub sysfs_write {
    my ($p, $value) = @_;
    my ($path) = ($p // '') =~ m|^(/sys/[A-Za-z0-9_./:-]+)$| or die "storwize: invalid sysfs path '$p'\n";
    # Write sysfs attributes with a plain open/print: file_set_contents() uses a
    # temp file + rename, which does not work on sysfs.
    my $fh;
    if (!open($fh, '>', $path)) {
        warn "storwize: cannot open $path: $!\n";
        return 0;
    }
    my $ok = print $fh $value;
    $ok = close($fh) && $ok;
    warn "storwize: write to $path failed: $!\n" if !$ok;
    return $ok;
}

sub cache_read {
    my ($file, $ttl) = @_;
    return undef if !-f $file;
    my $age = time() - (stat($file))[9];
    return undef if defined($ttl) && $age > $ttl;
    my $raw = eval { PVE::Tools::file_get_contents($file) };
    return undef if !defined($raw);
    return eval { decode_json($raw) };
}

sub cache_write {
    my ($file, $data) = @_;
    eval { file_set_contents($file, encode_json($data), 0600) };
    warn "storwize: cache write $file failed: $@" if $@;
}

sub code_ge {
    my ($code_level, $want) = @_;
    my @a = ($code_level // '0') =~ m/(\d+)/g;
    my @b = split(/\./, $want);
    for my $i (0 .. $#b) {
        my $x = $a[$i] // 0;
        return 1 if $x > $b[$i];
        return 0 if $x < $b[$i];
    }
    return 1;
}

# ---------------------------------------------------------------------------
# Backend: one command layer, two transports
#
# sw_run($scfg, $storeid, $cmd, \@opts, $obj)
#   $cmd  : CLI command, e.g. 'lsvdisk', 'mkvdisk'
#   @opts : [ name => value ] pairs; value undef = flag (e.g. [bytes => undef])
#   $obj  : optional positional object (id or name)
# Returns, for both backends:
#   ls*  : arrayref of hashes (table) - or a hash with { detail => 1 } in %param
#   other: hash { id => ..., message => ... }
# ---------------------------------------------------------------------------

sub sw_run {
    my ($scfg, $storeid, $cmd, $opts, $obj, %param) = @_;

    $cmd = check_arg($cmd);
    die "storwize: odd option list for $cmd\n" if @{ $opts // [] } % 2;
    my @clean;
    for (my $i = 0; $i < @{ $opts // [] }; $i += 2) {
        push @clean, check_arg($opts->[$i]);
        push @clean, defined($opts->[$i + 1]) ? check_arg($opts->[$i + 1]) : undef;
    }
    $opts = \@clean;
    $obj = check_arg($obj) if defined($obj);

    my $be = backend($scfg, $storeid);
    return $be eq 'rest'
        ? rest_run($scfg, $storeid, $cmd, $opts, $obj, %param)
        : ssh_run($scfg, $storeid, $cmd, $opts, $obj, %param);
}

sub sw_list {
    my ($scfg, $storeid, $cmd, $opts, $obj) = @_;
    return sw_run($scfg, $storeid, $cmd, $opts, $obj, table => 1);
}

sub sw_show {
    my ($scfg, $storeid, $cmd, $opts, $obj) = @_;
    return sw_run($scfg, $storeid, $cmd, $opts, $obj, detail => 1);
}

# Returns undef instead of dying when the object does not exist.
sub sw_show_opt {
    my ($scfg, $storeid, $cmd, $opts, $obj) = @_;
    my $res = eval { sw_show($scfg, $storeid, $cmd, $opts, $obj) };
    if (my $err = $@) {
        return undef if $err =~ $NOTFOUND_RE;
        die $err;
    }
    return $res;
}

# --- SSH ---------------------------------------------------------------

sub ssh_cmd {
    my ($scfg, $storeid) = @_;
    my $rdir = run_dir($storeid);
    my ($key) = ($scfg->{'sw-sshkey'} // "$PRIV_DIR/${storeid}_id_rsa") =~ m|^(/[A-Za-z0-9_./-]+)$|
        or die "storwize: invalid sw-sshkey path\n";
    die "storwize: SSH key '$key' not found (run scripts/setup-ssh-key.sh $storeid)\n" if !-f $key;
    my ($user) = ($scfg->{'sw-user'} // '') =~ m/^([A-Za-z0-9_.@-]+)$/
        or die "storwize: sw-user not set or invalid\n";
    my ($addr) = ($scfg->{'sw-address'} // '') =~ m/^([A-Za-z0-9_.:\[\]-]+)$/
        or die "storwize: sw-address not set or invalid\n";
    my ($port) = ($scfg->{'sw-port'} // 22) =~ m/^(\d+)$/ or die "storwize: invalid sw-port\n";
    # The array checks the key on every command, also inside an open shared connection, and
    # pvestatd keeps that connection busy. The key file's change time in the socket name makes a
    # replaced key (on any node of the cluster) open a new connection; the old one ends by itself.
    my ($kt) = ((stat($key))[9] // 0) =~ m/^(\d+)$/;
    return [
        '/usr/bin/ssh',
        '-i', $key,
        '-p', $port,
        '-o', "UserKnownHostsFile=$KNOWN_HOSTS",
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=15',
        '-o', 'PubkeyAcceptedAlgorithms=+ssh-rsa',
        '-o', 'ControlMaster=auto',
        '-o', "ControlPath=$rdir/ssh-$kt-%C",
        '-o', 'ControlPersist=60',
        '-o', 'LogLevel=ERROR',
        "$user\@$addr",
    ];
}

sub ssh_run {
    my ($scfg, $storeid, $cmd, $opts, $obj, %param) = @_;

    my $is_ls = $cmd =~ m/^ls/;
    my @words = ($is_ls ? 'svcinfo' : 'svctask', $cmd);
    push @words, '-delim', "':'" if $is_ls;
    for (my $i = 0; $i < @$opts; $i += 2) {
        my ($k, $v) = ($opts->[$i], $opts->[$i + 1]);
        push @words, "-$k";
        push @words, "'$v'" if defined($v);
    }
    push @words, "'$obj'" if defined($obj);
    my $remote = join(' ', @words);

    my ($out, $err) = ('', '');
    my $rc = eval {
        run_command(
            [@{ ssh_cmd($scfg, $storeid) }, $remote],
            outfunc => sub { $out .= "$_[0]\n" },
            errfunc => sub { $err .= "$_[0]\n" },
            timeout => $param{timeout} // 120,
            noerr => 1,
        );
    };
    die "storwize: ssh failed ($cmd): $@" if $@;
    if ($rc != 0 || $err =~ m/CMMVC\d+E/) {
        my $msg = $err || $out;
        $msg =~ s/\s+$//;
        die "storwize: $cmd failed: $msg\n";
    }

    if ($is_ls) {
        return parse_detail($out) if $param{detail};
        return parse_table($out);
    }
    my $res = { message => ($out =~ s/\s+$//r) };
    $res->{id} = $1 if $out =~ m/id \[(\d+)\]/;
    return $res;
}

sub parse_table {
    my ($text) = @_;
    my @lines = grep { length } split(/\n/, $text);
    return [] if !@lines;
    my @hdr = split(/:/, shift(@lines), -1);
    my $rows = [];
    for my $l (@lines) {
        my @v = split(/:/, $l, -1);
        # a trailing value may contain ':' (e.g. iSCSI names): glue the rest
        if (@v > @hdr) { $v[$#hdr] = join(':', @v[$#hdr .. $#v]); $#v = $#hdr; }
        my %h;
        @h{@hdr} = @v;
        push @$rows, \%h;
    }
    return $rows;
}

sub parse_detail {
    my ($text) = @_;
    my $h = {};
    for my $l (split(/\n/, $text)) {
        next if $l !~ m/^([^:]+):(.*)$/;
        # detail views repeat keys per copy - keep the first (volume level)
        $h->{$1} = $2 if !exists($h->{$1});
    }
    return $h;
}

# --- REST --------------------------------------------------------------

sub rest_ua {
    my ($scfg) = @_;
    require LWP::UserAgent;
    require HTTP::Request;
    my %ssl;
    if (my $fp = $scfg->{'sw-fingerprint'}) {
        $fp =~ s/://g;
        %ssl = (SSL_verify_mode => 0, SSL_fingerprint => 'sha256$' . lc($fp), verify_hostname => 0);
    } else {
        %ssl = (SSL_verify_mode => 0, verify_hostname => 0);
    }
    return LWP::UserAgent->new(timeout => 60, ssl_opts => \%ssl, agent => "pve-ibm-storage/$VERSION");
}

sub rest_base {
    my ($scfg) = @_;
    my $addr = $scfg->{'sw-address'};
    $addr = "[$addr]" if $addr =~ m/:/;
    return "https://$addr:" . ($scfg->{'sw-port'} // 7443) . "/rest/v1";
}

sub rest_token {
    my ($scfg, $storeid, $renew) = @_;
    my $tfile = run_dir($storeid) . "/token";
    if (!$renew && -f $tfile && (time() - (stat($tfile))[9]) < 1800) {
        my $t = file_read_firstline($tfile);
        return $t if $t;
    }
    my $pw = get_password($storeid) // die "storwize: no password stored for '$storeid'\n";
    my $req = HTTP::Request->new(POST => rest_base($scfg) . "/auth");
    $req->header('X-Auth-Username' => $scfg->{'sw-user'});
    $req->header('X-Auth-Password' => $pw);
    my $res = rest_ua($scfg)->request($req);
    die "storwize: REST auth failed: " . $res->status_line . "\n" if !$res->is_success;
    my $tok = eval { decode_json($res->decoded_content)->{token} }
        or die "storwize: REST auth returned no token\n";
    file_set_contents($tfile, "$tok\n", 0600);
    return $tok;
}

sub rest_run {
    my ($scfg, $storeid, $cmd, $opts, $obj, %param) = @_;

    my %body;
    for (my $i = 0; $i < @$opts; $i += 2) {
        my ($k, $v) = ($opts->[$i], $opts->[$i + 1]);
        $body{$k} = defined($v) ? $v : JSON::true;
    }
    my $url = rest_base($scfg) . "/$cmd" . (defined($obj) ? "/$obj" : '');

    my $ua = rest_ua($scfg);
    my $res;
    for my $try (0, 1) {
        my $req = HTTP::Request->new(POST => $url);
        $req->header('X-Auth-Token' => rest_token($scfg, $storeid, $try));
        $req->header('Content-Type' => 'application/json');
        $req->content(encode_json(\%body));
        $res = $ua->request($req);
        last if $res->code != 401 && $res->code != 403;
    }
    if (!$res->is_success) {
        my $msg = $res->decoded_content // '';
        $msg =~ s/\s+$//;
        die "storwize: $cmd failed: " . $res->status_line . ($msg ? " - $msg" : '') . "\n";
    }
    my $content = $res->decoded_content;
    my $data = length($content // '') ? eval { decode_json($content) } : undef;

    if ($cmd =~ m/^ls/) {
        if ($param{detail}) {
            return $data->[0] // {} if ref($data) eq 'ARRAY';
            return $data // {};
        }
        return [] if !defined($data);
        return ref($data) eq 'ARRAY' ? $data : [$data];
    }
    return $data if ref($data) eq 'HASH';
    return { message => $content };
}

# ---------------------------------------------------------------------------
# Storage-side lookups
# ---------------------------------------------------------------------------

sub system_info {
    my ($scfg, $storeid, $nocache) = @_;
    my $cfile = run_dir($storeid) . "/system.json";
    if (!$nocache) {
        my $c = cache_read($cfile, $SYSTEM_CACHE_TTL);
        return $c if $c;
    }
    my $sys = sw_show($scfg, $storeid, 'lssystem');
    my $info = {
        name => $sys->{name},
        code_level => $sys->{code_level},
        product_name => $sys->{product_name},
        id => $sys->{id},
    };
    cache_write($cfile, $info);
    return $info;
}

# Replication mode of this system for a volume group under policy-based replication
# (production / recovery / independent); undef when the group is not replicated.
# Not validated on a real 8.x system yet.
sub replication_local_mode {
    my ($scfg, $storeid, $vg) = @_;
    my $r = eval { sw_show_opt($scfg, $storeid, 'lsvolumegroupreplication', [], $vg) };
    return undef if !$r || !$r->{replication_policy_name};
    my $sys = system_info($scfg, $storeid);
    for my $n (1, 2) {
        return $r->{"location${n}_replication_mode"}
            if ($r->{"location${n}_system_name"} // '') eq ($sys->{name} // "\0")
            || ($r->{"location${n}_system_id"} // '') eq ($sys->{id} // "\0");
    }
    return 'unknown';
}

sub vdisk_info {
    my ($scfg, $storeid, $vname) = @_;
    return sw_show_opt($scfg, $storeid, 'lsvdisk', [bytes => undef], $vname);
}

sub uid_cache_file {
    my ($storeid, $volname) = @_;
    return run_dir($storeid) . "/$volname.uid";
}

sub volume_uid {
    my ($scfg, $storeid, $volname, $refresh) = @_;
    my $cf = uid_cache_file($storeid, $volname);
    if (!$refresh && -f $cf) {
        my $u = file_read_firstline($cf);
        return $1 if $u && $u =~ m/^([0-9A-Fa-f]{32})$/;
    }
    my $vname = vdisk_name($scfg, $volname);
    my $info = vdisk_info($scfg, $storeid, $vname)
        or die "storwize: volume '$vname' does not exist on the storage\n";
    my $uid = $info->{vdisk_UID} // die "storwize: no vdisk_UID for '$vname'\n";
    ($uid) = $uid =~ m/^([0-9A-Fa-f]{32})$/ or die "storwize: unexpected vdisk_UID '$uid'\n";
    file_set_contents($cf, "$uid\n", 0600);
    return $uid;
}

sub uid_to_wwid {
    my ($uid) = @_;
    my ($u) = ($uid // '') =~ m/^([0-9A-Fa-f]{32})$/ or die "storwize: invalid vdisk_UID '" . ($uid // '') . "'\n";
    return '3' . lc($u);
}

sub uid_to_path { my ($uid) = @_; return "/dev/disk/by-id/dm-uuid-mpath-" . uid_to_wwid($uid); }

# FlashCopy maps whose source is $vname (= its snapshots)
sub snapshot_maps {
    my ($scfg, $storeid, $vname) = @_;
    return sw_list($scfg, $storeid, 'lsfcmap', [filtervalue => "source_vdisk_name=$vname"]);
}

# FlashCopy maps whose target is $vname (= rollbacks writing into it)
sub restore_maps {
    my ($scfg, $storeid, $vname) = @_;
    return sw_list($scfg, $storeid, 'lsfcmap', [filtervalue => "target_vdisk_name=$vname"]);
}

# ---------------------------------------------------------------------------
# Host side: SCSI / multipath
# ---------------------------------------------------------------------------

sub rescan_hosts {
    my ($scfg) = @_;
    my $t = $scfg->{'sw-transport'} // 'auto';
    if ($t eq 'fc' || $t eq 'auto') {
        for my $h (glob('/sys/class/fc_host/host*')) {
            my ($n) = $h =~ m|/(host\d+)$| or next;
            sysfs_write("/sys/class/scsi_host/$n/scan", "- - -\n");
        }
    }
    if (($t eq 'iscsi' || $t eq 'auto') && -x '/usr/bin/iscsiadm') {
        eval {
            run_command(['/usr/bin/iscsiadm', '-m', 'session', '--rescan'],
                outfunc => sub { }, errfunc => sub { }, noerr => 1, timeout => 60);
        };
    }
}

sub dm_slaves {
    my ($dev) = @_;
    my $real = abs_path($dev) // return ();
    my ($dm) = $real =~ m|/dev/(dm-\d+)$| or return ();
    return map { m|/([A-Za-z0-9_-]+)$| ? $1 : () } glob("/sys/block/$dm/slaves/*");
}

sub udev_settle {
    eval { run_command(['/usr/bin/udevadm', 'settle', '--timeout=15'], noerr => 1) };
}

sub multipath_add {
    my ($wwid) = @_;
    for my $c (['/usr/sbin/multipath', '-a', $wwid], ['/usr/sbin/multipath', $wwid]) {
        eval { run_command($c, outfunc => sub { }, errfunc => sub { }, noerr => 1, timeout => 60) };
    }
}

sub device_rescan_size {
    my ($dev, $wwid) = @_;
    for my $sd (dm_slaves($dev)) {
        sysfs_write("/sys/block/$sd/device/rescan", "1\n");
    }
    eval {
        run_command(['/usr/sbin/multipathd', 'resize', 'map', $wwid],
            outfunc => sub { }, errfunc => sub { }, noerr => 1, timeout => 60);
    };
    udev_settle();
}

sub device_remove_local {
    my ($uid) = @_;
    my $dev = uid_to_path($uid);
    my $wwid = uid_to_wwid($uid);
    my @slaves = -e $dev ? dm_slaves($dev) : ();
    if (-e $dev) {
        eval { run_command(['/usr/bin/blockdev', '--flushbufs', $dev], noerr => 1) };
        my $rc = eval {
            run_command(['/usr/sbin/multipath', '-f', $wwid],
                outfunc => sub { }, errfunc => sub { }, noerr => 1, timeout => 60);
        } // 1;
        die "storwize: could not flush multipath map $wwid (device still in use?)\n"
            if $rc != 0 && -e $dev;
    }
    for my $sd (@slaves) {
        sysfs_write("/sys/block/$sd/device/delete", "1\n") if -e "/sys/block/$sd/device/delete";
    }
    eval {
        run_command(['/usr/sbin/multipath', '-w', $wwid],
            outfunc => sub { }, errfunc => sub { }, noerr => 1);
    };
}

# ---------------------------------------------------------------------------
# Host side: NVMe over TCP (native NVMe multipath, no dm-multipath)
# Not validated on real hardware yet - see docs/NVME-TCP.md
# ---------------------------------------------------------------------------

sub is_nvme { my ($scfg) = @_; return ($scfg->{'sw-transport'} // 'auto') eq 'nvme-tcp'; }

sub nvme_norm {
    my ($v) = @_;
    $v = lc($v // '');
    $v =~ s/^(?:eui|uuid|nvme)\.//;
    $v =~ s/[^0-9a-f]//g;
    return $v;
}

sub sysfs_read {
    my ($f) = @_;
    open(my $fh, '<', $f) or return undef;
    my $l = <$fh>;
    close($fh);
    return undef if !defined($l);
    my ($v) = $l =~ m/^\s*([A-Za-z0-9 ._:-]+?)\s*$/ or return undef;      # untaint
    return $v;
}

# Identifiers the array may report for the namespace of a volume. With NVMe hosts the
# volume UUID takes the NGUID format, so every UID-like field of lsvdisk is a candidate.
sub nvme_candidates {
    my ($info) = @_;
    my %c;
    for my $k (qw(vdisk_UID volume_uuid uuid UUID nguid NGUID)) {
        my $v = nvme_norm($info->{$k});
        $c{$v} = 1 if length($v) >= 16;
    }
    return sort keys %c;
}

# Namespaces (multipath head devices) seen by this node: [ block name, wwid, normalized ids ]
sub nvme_namespaces {
    my ($root) = @_;
    $root //= '/sys/block';
    my @out;
    for my $b (glob("$root/nvme*n*")) {
        my ($name) = $b =~ m|/(nvme\d+n\d+)$| or next;          # skips per-path nvmeXcYnZ
        my $wwid = sysfs_read("$b/wwid") // next;
        my %ids = map { $_ => 1 } grep { length($_) >= 16 && $_ !~ m/^0+$/ }
            map { nvme_norm(sysfs_read("$b/$_")) } qw(wwid nguid uuid);
        push @out, [$name, $wwid, [sort keys %ids]];
    }
    return @out;
}

sub nvme_find {
    my ($cand, $root) = @_;
    my %want = map { $_ => 1 } @$cand;
    for my $ns (nvme_namespaces($root)) {
        return $ns if grep { $want{$_} } @{ $ns->[2] };
    }
    return undef;
}

sub nvme_wwid_file { my ($storeid, $volname) = @_; return run_dir($storeid) . "/nvme-$volname"; }

# Block device of a volume: dm-multipath map (FC/iSCSI) or the by-id link of the NVMe namespace.
sub volume_dev {
    my ($scfg, $storeid, $volname, $uid) = @_;
    return uid_to_path($uid) if !is_nvme($scfg);
    my $w = -f nvme_wwid_file($storeid, $volname) ? file_read_firstline(nvme_wwid_file($storeid, $volname)) : undef;
    ($w) = ($w // '') =~ m/^([A-Za-z0-9._:-]+)$/;
    return "/dev/disk/by-id/nvme-$w" if $w;
    return "/dev/disk/by-id/nvme-eui." . lc($uid);              # best guess until the first activation
}

sub nvme_controllers {
    return grep { (sysfs_read("$_/model") // '') =~ m/IBM|2145|FlashSystem/i } glob('/sys/class/nvme/nvme*');
}

sub nvme_rescan {
    sysfs_write("$_/rescan_controller", "1\n") for nvme_controllers();
    udev_settle();
}

sub nvme_connect {
    my ($scfg) = @_;
    return if grep { (sysfs_read("$_/state") // '') eq 'live' } nvme_controllers();
    my $bin = -x '/usr/sbin/nvme' ? '/usr/sbin/nvme' : '/usr/bin/nvme';
    die "storwize: nvme-cli is not installed on this node\n" if !-x $bin;
    my @portals = grep { length } split(/,/, $scfg->{'sw-portals'} // '');
    die "storwize: sw-transport=nvme-tcp needs sw-portals (IP[:port] of the array's NVMe/TCP ports)\n" if !@portals;
    for my $p (@portals) {
        my ($ip, $port) = $p =~ m/^\[?([0-9A-Fa-f:.]+?)\]?(?::(\d+))?$/ or next;
        eval {
            run_command([$bin, 'connect-all', '-t', 'tcp', '-a', $ip, '-s', $port // 4420],
                outfunc => sub { }, errfunc => sub { }, noerr => 1, timeout => 60);
        };
    }
    udev_settle();
}

sub nvme_activate {
    my ($scfg, $storeid, $volname) = @_;
    my $info = vdisk_info($scfg, $storeid, vdisk_name($scfg, $volname))
        or die "storwize: volume '$volname' does not exist\n";
    my @cand = nvme_candidates($info);
    my $deadline = time() + $DEV_WAIT;
    my $n = 0;
    while (1) {
        if (my $ns = nvme_find(\@cand)) {
            file_set_contents(nvme_wwid_file($storeid, $volname), "$ns->[1]\n", 0600);
            my $dev = "/dev/disk/by-id/nvme-$ns->[1]";
            udev_settle() if !-b $dev;
            return 1 if -b $dev;
        }
        if (time() > $deadline) {
            my $seen = join(', ', map { "$_->[0]=$_->[1]" } nvme_namespaces()) || 'none';
            die "storwize: NVMe namespace of '$volname' did not appear after ${DEV_WAIT}s"
                . " (array ids: @cand; namespaces on this node: $seen)\n";
        }
        nvme_connect($scfg) if $n % 10 == 0;
        nvme_rescan() if $n % 3 == 0;
        $n++;
        sleep(1);
    }
}

# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------

sub map_vdisk {
    my ($scfg, $storeid, $vname) = @_;
    my $t = mapping_targets($scfg);
    if ($t->{hostcluster}) {
        sw_run($scfg, $storeid, 'mkvolumehostclustermap', [hostcluster => $t->{hostcluster}], $vname);
    } else {
        for my $h (@{ $t->{hosts} }) {
            # -force: allow mapping the same volume to several hosts
            sw_run($scfg, $storeid, 'mkvdiskhostmap', [force => undef, host => $h], $vname);
        }
    }
}

sub unmap_vdisk {
    my ($scfg, $storeid, $vname) = @_;
    my $t = mapping_targets($scfg);
    my $maps = eval { sw_list($scfg, $storeid, 'lsvdiskhostmap', [], $vname) } // [];
    if ($t->{hostcluster}) {
        my $has = grep { ($_->{host_cluster_name} // '') eq $t->{hostcluster} } @$maps;
        sw_run($scfg, $storeid, 'rmvolumehostclustermap', [hostcluster => $t->{hostcluster}], $vname)
            if $has;
    }
    my %ours = map { $_ => 1 } @{ $t->{hosts} // [] };
    for my $m (@$maps) {
        next if ($m->{host_cluster_name} // '') ne '';
        my $host = $m->{host_name} // '';    # lsvdiskhostmap: 'name' is the volume
        next if !$ours{$host};
        sw_run($scfg, $storeid, 'rmvdiskhostmap', [host => $host], $vname);
    }
}

# ---------------------------------------------------------------------------
# Storage API: names and paths
# ---------------------------------------------------------------------------

sub parse_volname {
    my ($class, $volname) = @_;
    if ($volname =~ $VOLNAME_RE) {
        return ('images', $volname, $1, undef, undef, 0, 'raw');
    }
    die "unable to parse storwize volume name '$volname'\n";
}

sub filesystem_path {
    my ($class, $scfg, $volname, $snapname) = @_;
    die "storwize: direct access to snapshots is not supported\n" if defined($snapname);
    # storeid is not passed here; path() is the real entry point
    die "storwize: filesystem_path() needs a storage id - use path()\n";
}

# ---- 8.5+ (sw-snapmode=volumegroup): read access to a snapshot ---------------------
# Proxmox reads a snapshot through a block device (full clone from a snapshot). The array
# gives one by a thinclone of the volume group snapshot: a new volume, created at once
# without copying data, in its own volume group <prefix>-snapro-<vmid>-<epoch>-<id>. It is
# removed when Proxmox deactivates the snapshot, and by the hourly cleanup after a day
# (Proxmox does not deactivate the source when the VM is running).
my $SNAPRO_KEEP = 86400;

sub snapro_key {
    my ($volname, $snap) = @_;
    require Digest::MD5;
    return substr(Digest::MD5::md5_hex("$volname\@$snap"), 0, 6);
}

sub snapro_file { my ($storeid, $key) = @_; return run_dir($storeid) . "/snapro-$key"; }

sub snapro_find {
    my ($scfg, $storeid, $volname, $snap) = @_;
    my ($vmid) = $volname =~ m/^vm-(\d+)-/ or return undef;
    my $p = sw_prefix($scfg);
    my $key = snapro_key($volname, $snap);
    my $rows = sw_list($scfg, $storeid, 'lsvolumegroup', [filtervalue => "name=$p-snapro-$vmid-*"]);
    my ($g) = grep { ($_->{name} // '') =~ m/^\Q$p\E-snapro-\Q$vmid\E-\d+-\Q$key\E$/ } @$rows;
    return undef if !$g;
    my $vols = sw_list($scfg, $storeid, 'lsvdisk', [filtervalue => "volume_group_name=$g->{name}"]);
    return { vg => $g->{name}, vol => $vols->[0] };
}

sub snapro_wait_dev {
    my ($scfg, $uid) = @_;
    my $dev = uid_to_path($uid);
    my $wwid = uid_to_wwid($uid);
    my $deadline = time() + $DEV_WAIT;
    my $n = 0;
    while (!-b $dev) {
        die "storwize: device $dev did not appear after ${DEV_WAIT}s\n" if time() > $deadline;
        if ($n++ % 5 == 0) {
            rescan_hosts($scfg);
            udev_settle();
            multipath_add($wwid);
        }
        sleep(1);
    }
    return $dev;
}

sub snapro_activate {
    my ($class, $scfg, $storeid, $volname, $snap) = @_;
    die "storwize: reading a snapshot needs sw-snapmode=volumegroup\n" if snapmode($scfg) ne 'volumegroup';
    die "storwize: reading a snapshot is not supported over NVMe/TCP yet\n" if is_nvme($scfg);
    my (undef, undef, $vmid) = $class->parse_volname($volname);
    my $gvg = guest_vg_name($scfg, $vmid);
    die "storwize: snapshot '$snap' not found in volume group '$gvg'\n"
        if !vg_snapshot_exists($scfg, $storeid, $gvg, $snap);
    my $key = snapro_key($volname, $snap);
    my $sr = snapro_find($scfg, $storeid, $volname, $snap);
    if (!$sr || !$sr->{vol}) {
        sw_run($scfg, $storeid, 'rmvolumegroup', [], $sr->{vg}) if $sr;     # leftover, empty
        my $vg = sw_prefix($scfg) . "-snapro-$vmid-" . time() . "-$key";
        sw_run($scfg, $storeid, 'mkvolumegroup', [
            type => 'thinclone', snapshot => $snap, fromsourcegroup => $gvg,
            fromsourcevolumes => vdisk_name($scfg, $volname), name => $vg, pool => $scfg->{'sw-pool'},
        ]);
        $sr = snapro_find($scfg, $storeid, $volname, $snap)
            or die "storwize: thinclone of '$volname' at '$snap' was not created\n";
        die "storwize: thinclone group '$sr->{vg}' has no volume\n" if !$sr->{vol};
        # the array names it <source>-<n>, i.e. like a disk of the guest: rename it out of the
        # <prefix>-vm-* namespace so Proxmox never sees it (qm rescan would add it as unused)
        sw_run($scfg, $storeid, 'chvdisk', [name => $sr->{vg}], $sr->{vol}{id});
        $sr = snapro_find($scfg, $storeid, $volname, $snap);
    }
    my $v = $sr->{vol}{name};
    my $maps = eval { sw_list($scfg, $storeid, 'lsvdiskhostmap', [], $v) } // [];
    map_vdisk($scfg, $storeid, $v) if !@$maps;
    my $info = vdisk_info($scfg, $storeid, $v) or die "storwize: '$v' not found\n";
    file_set_contents(snapro_file($storeid, $key), "$info->{vdisk_UID}\n", 0600);
    snapro_wait_dev($scfg, $info->{vdisk_UID});
    return 1;
}

sub snapro_remove {
    my ($scfg, $storeid, $vg, $vol) = @_;
    if ($vol) {
        eval { device_remove_local($vol->{vdisk_UID}) } if $vol->{vdisk_UID};
        unmap_vdisk($scfg, $storeid, $vol->{name});
        sw_run($scfg, $storeid, 'rmvdisk', [], $vol->{name});
    }
    sw_run($scfg, $storeid, 'rmvolumegroup', [], $vg);
}

sub snapro_deactivate {
    my ($scfg, $storeid, $volname, $snap) = @_;
    my $sr = snapro_find($scfg, $storeid, $volname, $snap) or return 1;
    snapro_remove($scfg, $storeid, $sr->{vg}, $sr->{vol});
    unlink snapro_file($storeid, snapro_key($volname, $snap));
    return 1;
}

sub snapro_purge {
    my ($scfg, $storeid) = @_;
    my $p = sw_prefix($scfg);
    my $rows = sw_list($scfg, $storeid, 'lsvolumegroup', [filtervalue => "name=$p-snapro-*"]);
    for my $g (@$rows) {
        my ($t) = ($g->{name} // '') =~ m/^\Q$p\E-snapro-\d+-(\d+)-[0-9a-f]{6}$/ or next;
        next if time() - $t < $SNAPRO_KEEP;
        eval {
            my $vols = sw_list($scfg, $storeid, 'lsvdisk', [filtervalue => "volume_group_name=$g->{name}"]);
            snapro_remove($scfg, $storeid, $g->{name}, $vols->[0]);
        };
        warn "storwize: snapshot access '$g->{name}' kept: $@" if $@;
    }
}

sub path {
    my ($class, $scfg, $volname, $storeid, $snapname) = @_;
    if (defined($snapname)) {
        my $f = snapro_file($storeid, snapro_key($volname, $snapname));
        my ($uid) = (-f $f ? file_read_firstline($f) : '') =~ m/^([0-9A-Fa-f]{32})$/
            or die "storwize: snapshot '$snapname' of '$volname' is not active\n";
        my (undef, undef, $vmid) = $class->parse_volname($volname);
        return wantarray ? (uid_to_path($uid), $vmid, 'images') : uid_to_path($uid);
    }
    my ($vtype, undef, $vmid) = $class->parse_volname($volname);
    my $uid = volume_uid($scfg, $storeid, $volname);
    my $dev = volume_dev($scfg, $storeid, $volname, $uid);
    return wantarray ? ($dev, $vmid, $vtype) : $dev;
}

# ---------------------------------------------------------------------------
# Storage API: lifecycle
# ---------------------------------------------------------------------------

sub other_storwize_conflict {
    my ($storeid, $scfg) = @_;
    my $cfg = eval { PVE::Storage::config() } or return;
    for my $id (sort keys %{ $cfg->{ids} }) {
        next if $id eq $storeid;
        my $o = $cfg->{ids}->{$id};
        next if ($o->{type} // '') !~ m/^(?:storwize|flashsystem)$/;      # both types of this plugin
        next if lc($o->{'sw-address'} // '') ne lc($scfg->{'sw-address'} // '');
        die "storwize: storage '$id' already manages prefix '" . sw_prefix($scfg)
            . "' on $scfg->{'sw-address'} - use a different sw-prefix\n"
            if sw_prefix($o) eq sw_prefix($scfg);
    }
}

sub fetch_fingerprint {
    my ($scfg) = @_;
    require IO::Socket::SSL;
    my $sock = IO::Socket::SSL->new(
        PeerHost => $scfg->{'sw-address'},
        PeerPort => $scfg->{'sw-port'} // 7443,
        SSL_verify_mode => 0,
        Timeout => 10,
    ) or die "storwize: TLS connect failed: " . IO::Socket::SSL::errstr() . "\n";
    my $fp = $sock->get_fingerprint('sha256');
    close($sock);
    $fp =~ s/^sha256\$//;
    return uc(join(':', $fp =~ m/(..)/g));
}

sub verify_config {
    my ($class, $storeid, $scfg) = @_;

    my $sys = system_info($scfg, $storeid, 1);
    print "storwize: connected to $sys->{name} ($sys->{product_name}, code $sys->{code_level})"
        . " via " . backend($scfg, $storeid) . "\n";

    my $pool = sw_show_opt($scfg, $storeid, 'lsmdiskgrp', [bytes => undef], $scfg->{'sw-pool'})
        or die "storwize: pool '$scfg->{'sw-pool'}' not found\n";
    if (my $sp = $scfg->{'sw-snappool'}) {
        sw_show_opt($scfg, $storeid, 'lsmdiskgrp', [], $sp)
            or die "storwize: snapshot pool '$sp' not found\n";
    }
    my $t = mapping_targets($scfg);
    if ($t->{hostcluster}) {
        die "storwize: host clusters need code level 7.7.1+\n"
            if !code_ge($sys->{code_level}, '7.7.1');
        sw_show_opt($scfg, $storeid, 'lshostcluster', [], $t->{hostcluster})
            or die "storwize: host cluster '$t->{hostcluster}' not found\n";
    } else {
        for my $h (@{ $t->{hosts} }) {
            sw_show_opt($scfg, $storeid, 'lshost', [], $h)
                or die "storwize: host '$h' not found\n";
        }
    }
    if (($scfg->{'sw-snapmode'} // 'flashcopy') eq 'volumegroup') {
        die "storwize: sw-snapmode=volumegroup needs code level 8.5.1+ (system has $sys->{code_level})\n"
            if !code_ge($sys->{code_level}, '8.5.1');
        die "storwize: sw-snapmode=volumegroup puts every guest in its own volume group -"
            . " it cannot be combined with sw-volumegroup\n" if $scfg->{'sw-volumegroup'};
    }
    die "storwize: sw-transport=nvme-tcp needs sw-portals\n"
        if ($scfg->{'sw-transport'} // 'auto') eq 'nvme-tcp' && !$scfg->{'sw-portals'};
    if (my $part = $scfg->{'sw-partition'}) {
        # inside a storage partition every volume lives in a volume group and hosts only map
        # volumes of their own partition; legacy FlashCopy targets do not fit that model
        die "storwize: sw-partition needs code level 8.6.1+ (system has $sys->{code_level})\n"
            if !code_ge($sys->{code_level}, '8.6.1');
        die "storwize: sw-partition needs sw-snapmode=volumegroup\n"
            if ($scfg->{'sw-snapmode'} // 'flashcopy') ne 'volumegroup';
        my $p = sw_show_opt($scfg, $storeid, 'lspartition', [], $part)
            or die "storwize: storage partition '$part' not found\n";
        die "storwize: storage partition '$part' is still a draft - publish it first\n"
            if ($p->{draft} // 'no') eq 'yes';
    }
    if (my $vg = $scfg->{'sw-volumegroup'}) {
        die "storwize: sw-volumegroup needs code level 8.3.0+ (system has $sys->{code_level})\n"
            if !code_ge($sys->{code_level}, '8.3.0');
        sw_show_opt($scfg, $storeid, 'lsvolumegroup', [], $vg)
            or die "storwize: volume group '$vg' not found\n";
    }
    return $sys;
}

# Store SSH material handed over through the API (e.g. by the web panel).
# Returns the list of files written (so a failed add can undo them).
sub store_ssh_material {
    my ($storeid, $scfg, %param) = @_;
    require MIME::Base64;
    my @written;
    make_path($PRIV_DIR, { mode => 0700 }) if !-d $PRIV_DIR;
    my ($id) = $storeid =~ m/^([A-Za-z][A-Za-z0-9_.-]*)$/ or die "invalid storage id\n";

    if (defined(my $b64 = $param{'sw-sshprivkey'})) {
        my $pem = MIME::Base64::decode_base64($b64);
        die "storwize: sw-sshprivkey is not a PEM/OpenSSH private key\n"
            if $pem !~ m/^-----BEGIN [A-Z ]*PRIVATE KEY-----\n[A-Za-z0-9+\/=\n]+-----END [A-Z ]*PRIVATE KEY-----\n?$/;
        my $file = "$PRIV_DIR/${id}_id_rsa";
        die "storwize: refusing to overwrite existing key $file - remove it first\n"
            if -e $file && !$param{_update};
        $pem .= "\n" if $pem !~ m/\n$/;
        ($pem) = $pem =~ m/^(.*)$/s;
        file_set_contents($file, $pem, 0600);
        push @written, $file;
        # ssh offers the key from the .pub file next to it: an old one there gets the new key refused
        my $pub = '';
        eval { run_command(['/usr/bin/ssh-keygen', '-y', '-f', $file], outfunc => sub { $pub .= "$_[0]\n" }, timeout => 10) };
        if ($pub =~ m/^((?:ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp\d+) [A-Za-z0-9+\/=]+)/) {
            file_set_contents("$file.pub", "$1 pveplugin\@$id\n", 0600);
            push @written, "$file.pub";
        } else {
            unlink "$file.pub";
        }
    }
    if (defined(my $b64 = $param{'sw-sshhostkey'})) {
        my $addr = $scfg->{'sw-address'} // die "storwize: sw-address not set\n";
        my @new;
        for my $l (split(/\n/, MIME::Base64::decode_base64($b64))) {
            next if $l =~ m/^\s*(#|$)/;
            my ($h, $t, $k) = $l =~ m/^(\S+) (ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp(?:256|384|521)) ([A-Za-z0-9+\/=]+)\s*$/
                or die "storwize: invalid host key line\n";
            die "storwize: host key is for '$h', not '$addr'\n" if lc($h) ne lc($addr);
            push @new, "$h $t $k";
        }
        die "storwize: no host key given\n" if !@new;
        my @keep;
        if (-f $KNOWN_HOSTS) {
            @keep = grep { (split(/\s+/, $_))[0] ne $addr }
                split(/\n/, PVE::Tools::file_get_contents($KNOWN_HOSTS));
        }
        my ($content) = join("\n", @keep, @new, '') =~ m/^(.*)$/s;
        file_set_contents($KNOWN_HOSTS, $content, 0600);
    }
    return @written;
}

sub on_add_hook {
    my ($class, $storeid, $scfg, %param) = @_;

    other_storwize_conflict($storeid, $scfg);

    my @written = store_ssh_material($storeid, $scfg, %param);
    if (defined(my $pw = $param{password})) {
        set_password($storeid, $pw);
    }
    eval {
        if (backend($scfg, $storeid) eq 'rest' && !$scfg->{'sw-fingerprint'}) {
            $scfg->{'sw-fingerprint'} = fetch_fingerprint($scfg);
            print "storwize: pinned REST certificate fingerprint $scfg->{'sw-fingerprint'}\n";
        }
        $class->verify_config($storeid, $scfg);
    };
    if (my $err = $@) {
        unlink password_file($storeid);
        unlink $_ for @written;
        die $err;
    }
    return undef;
}

# Proxmox VE 9 (storage API 13+) calls this one, with the current configuration apart from the
# properties being changed; the old hook received only the changed properties (no sw-address).
sub on_update_hook_full {
    my ($class, $storeid, $scfg, $update, $delete, $sensitive) = @_;
    my %cfg = (%$scfg, %$update);
    return $class->on_update_hook($storeid, \%cfg, %{ $sensitive // {} });
}

sub on_update_hook {
    my ($class, $storeid, $scfg, %param) = @_;
    store_ssh_material($storeid, $scfg, %param, _update => 1)
        if defined($param{'sw-sshprivkey'}) || defined($param{'sw-sshhostkey'});
    if (exists($param{password})) {
        if (defined($param{password})) {
            set_password($storeid, $param{password});
        } else {
            unlink password_file($storeid);
        }
        unlink run_dir($storeid) . "/token";
    }
    return undef;
}

# Every VM/CT config of the cluster (all nodes, including snapshot sections,
# unused disks and vmstate) that still references a volume of $storeid.
sub storage_users {
    my ($storeid) = @_;
    my $re = qr/(?:^|[\s,=])\Q$storeid\E:(vm-\d+-[A-Za-z0-9][A-Za-z0-9_-]*)/;
    my %users;
    for my $f (glob('/etc/pve/nodes/*/qemu-server/*.conf'), glob('/etc/pve/nodes/*/lxc/*.conf')) {
        my ($kind, $vmid) = $f =~ m|/(qemu-server\|lxc)/(\d+)\.conf$| or next;
        open(my $fh, '<', $f) or next;
        my $section = '';
        while (my $l = <$fh>) {
            $section = $1 if $l =~ m/^\[([^\]]+)\]/;
            next if $l !~ m/^(\w+):\s*(.*)$/;
            my ($key, $val) = ($1, $2);
            while ($val =~ m/$re/g) {
                my $what = $section ? "$key (snapshot $section)" : $key;
                push @{ $users{($kind eq 'lxc' ? 'CT' : 'VM') . " $vmid"} }, "$what=$1";
            }
        }
        close($fh);
    }
    return \%users;
}

sub on_delete_hook {
    my ($class, $storeid, $scfg) = @_;

    # PVE itself does not check if a storage is in use before removing it.
    # Refuse while any guest still references one of its volumes.
    my $users = storage_users($storeid);
    if (my @g = sort keys %$users) {
        my @list = map { "$_: " . join(', ', @{ $users->{$_} }) } @g;
        die "storwize: storage '$storeid' is still used by " . scalar(@g) . " guest(s) - "
            . "move or remove these disks first (qm disk move / pct move-volume):\n  "
            . join("\n  ", @list) . "\n";
    }

    unlink password_file($storeid);
    if (-d "$RUN_DIR/$storeid") {
        eval { File::Path::remove_tree("$RUN_DIR/$storeid") };
    }
    return undef;
}

sub check_connection {
    my ($class, $storeid, $scfg) = @_;
    return eval { system_info($scfg, $storeid); 1 } ? 1 : 0;
}

sub activate_storage {
    my ($class, $storeid, $scfg, $cache) = @_;
    run_dir($storeid);
    return 1;
}

sub deactivate_storage { return 1; }

sub status {
    my ($class, $storeid, $scfg, $cache) = @_;
    my $cfile = run_dir($storeid) . "/status.json";
    my $c = cache_read($cfile, $STATUS_CACHE_TTL);
    if (!$c) {
        my $p = eval { sw_show($scfg, $storeid, 'lsmdiskgrp', [bytes => undef], $scfg->{'sw-pool'}) };
        if (my $err = $@) {
            warn $err;
            return (0, 0, 0, 0);
        }
        my $total = int($p->{capacity} // 0);
        my $free = int($p->{free_capacity} // 0);
        $c = { total => $total, free => $free, used => $total - $free };
        cache_write($cfile, $c);
    }
    my $stamp = run_dir($storeid) . "/trash-purge";
    my $last = (stat($stamp))[9] // 0;
    if (time() - $last >= 3600) {
        file_set_contents($stamp, time() . "\n", 0600);
        eval { trash_purge($scfg, $storeid) };
        warn "storwize: trash: $@" if $@;
        eval { snapro_purge($scfg, $storeid) } if snapmode($scfg) eq 'volumegroup';
        warn "storwize: snapshot access: $@" if $@;
    }
    return ($c->{total}, $c->{free}, $c->{used}, 1);
}

sub list_images {
    my ($class, $storeid, $scfg, $vmid, $vollist, $cache) = @_;

    my $p = sw_prefix($scfg);
    my $filter = defined($vmid) ? "name=$p-vm-$vmid-*" : "name=$p-vm-*";
    my $rows = sw_list($scfg, $storeid, 'lsvdisk', [bytes => undef, filtervalue => $filter]);

    my %want = map { $_ => 1 } @{ $vollist // [] };
    my $res = [];
    for my $r (@$rows) {
        my $name = $r->{name} // next;
        next if $name =~ m/_s_/;
        next if ($r->{volume_group_name} // '') =~ m/^\Q$p\E-snapro-/;     # temporary snapshot access
        next if ($r->{mdisk_grp_name} // '') ne $scfg->{'sw-pool'}
            && ($r->{mdisk_grp_name} // '') ne 'many';
        my $volname = substr($name, length($p) + 1);
        my ($owner) = $volname =~ $VOLNAME_RE;
        next if !defined($owner);
        next if defined($vmid) && $owner != $vmid;
        my $volid = "$storeid:$volname";
        next if $vollist && !$want{$volid};
        push @$res, {
            volid => $volid, format => 'raw', size => int($r->{capacity} // 0), vmid => $owner,
        };
    }
    return $res;
}

sub join_guest_vg {
    my ($scfg, $storeid, $vmid, $vname) = @_;
    my $gvg = guest_vg_name($scfg, $vmid);
    sw_run($scfg, $storeid, 'mkvolumegroup', [name => $gvg])
        if !sw_show_opt($scfg, $storeid, 'lsvolumegroup', [], $gvg);
    sw_run($scfg, $storeid, 'chvdisk', [volumegroup => $gvg], $vname);
}

sub alloc_image {
    my ($class, $storeid, $scfg, $vmid, $fmt, $name, $size) = @_;

    die "storwize: unsupported format '$fmt' (raw only)\n" if defined($fmt) && $fmt ne 'raw';

    if (!defined($name)) {
        my $existing = $class->list_images($storeid, $scfg, $vmid);
        my %used = map { (split(/:/, $_->{volid}, 2))[1] => 1 } @$existing;
        for my $i (0 .. 999) {
            next if $used{"vm-$vmid-disk-$i"};
            $name = "vm-$vmid-disk-$i";
            last;
        }
        die "storwize: no free disk name for VM $vmid\n" if !defined($name);
    }
    my (undef, undef, $owner) = $class->parse_volname($name);
    die "storwize: volume '$name' does not belong to VM $vmid\n" if $owner != $vmid;

    my $vname = vdisk_name($scfg, $name);
    die "storwize: volume '$vname' already exists\n" if vdisk_info($scfg, $storeid, $vname);

    my $bytes = int($size) * 1024;     # $size is in KiB
    $bytes = int(($bytes + 511) / 512) * 512;
    die "storwize: invalid size\n" if $bytes <= 0;

    my @opts = (
        name => $vname,
        mdiskgrp => $scfg->{'sw-pool'},
        iogrp => $scfg->{'sw-iogrp'} // 'io_grp0',
        size => $bytes,
        unit => 'b',
    );
    # thin volume: no per-volume alert at 80% of its size (default), which fires for every guest
    # that fills its disk; with autoexpand only the pool capacity matters, and the array warns on it
    push @opts, (rsize => '2%', autoexpand => undef, warning => '0%') if $scfg->{'sw-thin'} // 1;
    my $part = $scfg->{'sw-partition'};
    if ($part) {
        # storage partition: the volume is born inside the guest's volume group (mkvolume
        # -volumegroup); only then can it be mapped to the hosts of the same partition
        my $gvg = guest_vg_name($scfg, $vmid);
        my $g = sw_show_opt($scfg, $storeid, 'lsvolumegroup', [], $gvg);
        if (!$g) {
            sw_run($scfg, $storeid, 'mkvolumegroup', [name => $gvg, partition => $part]);
        } elsif (($g->{partition_name} // '') ne $part) {
            die "storwize: volume group '$gvg' exists outside storage partition '$part'\n";
        }
        my @vopts = (name => $vname, pool => $scfg->{'sw-pool'}, size => $bytes, unit => 'b',
            volumegroup => $gvg);
        push @vopts, (iogrp => $scfg->{'sw-iogrp'}) if $scfg->{'sw-iogrp'};
        push @vopts, (thin => undef, buffersize => '2%') if $scfg->{'sw-thin'} // 1;
        sw_run($scfg, $storeid, 'mkvolume', \@vopts);
    } else {
        sw_run($scfg, $storeid, 'mkvdisk', \@opts);
    }

    eval {
        map_vdisk($scfg, $storeid, $vname);
        if ($part) {
            # already in its volume group
        } elsif (my $vg = $scfg->{'sw-volumegroup'}) {
            sw_run($scfg, $storeid, 'chvdisk', [volumegroup => $vg], $vname);
        } elsif (snapmode($scfg) eq 'volumegroup') {
            join_guest_vg($scfg, $storeid, $vmid, $vname);
        }
        volume_uid($scfg, $storeid, $name, 1);
    };
    if (my $err = $@) {
        eval { unmap_vdisk($scfg, $storeid, $vname) };
        eval { sw_run($scfg, $storeid, 'rmvdisk', [], $vname) };
        warn "storwize: cleanup after failed allocation: $@" if $@;
        die $err;
    }
    unlink run_dir($storeid) . "/status.json";
    return $name;
}

sub free_image {
    my ($class, $storeid, $scfg, $volname, $isBase, $format) = @_;

    my $vname = vdisk_name($scfg, $volname);
    my $info = vdisk_info($scfg, $storeid, $vname);
    if (!$info) {
        warn "storwize: volume '$vname' already gone\n";
        unlink uid_cache_file($storeid, $volname);
        return undef;
    }

    # 8.5.2+ policy-based replication: the recovery copy of a replicated volume is never
    # removed from here (delete the volume on the production system instead)
    if (my $vg = $info->{volume_group_name}) {
        my $mode = replication_local_mode($scfg, $storeid, $vg);
        die "storwize: '$vname' is the recovery copy of replicated volume group '$vg'"
            . " (mode $mode) - not removed\n" if $mode && $mode !~ m/^(production|independent)$/;
    }

    wait_rollbacks_done($scfg, $storeid, "target_vdisk_name=$vname", "a rollback into '$vname'");

    # A volume in the guest's own volume group (sw-snapmode=volumegroup) leaves the group and
    # goes to the trash; the group's snapshots keep their copy of it. Volumes in other groups
    # (replication, storage partitions, a fixed sw-volumegroup) are deleted as before.
    my (undef, undef, $owner) = $class->parse_volname($volname);
    my $vgn = $info->{volume_group_name} // '';
    my $own_vg = $vgn ne '' && $vgn eq guest_vg_name($scfg, $owner) && !$scfg->{'sw-partition'}
        && !replication_local_mode($scfg, $storeid, $vgn);
    my $trash = trash_days($scfg) > 0 && ($vgn eq '' || $own_vg)
        ? trash_name($scfg, $volname, time()) : undef;

    # FlashCopy cascade: remove snapshots oldest first
    my $maps = snapshot_maps($scfg, $storeid, $vname);
    for my $m (sort { ($a->{start_time} // '') cmp ($b->{start_time} // '') } @$maps) {
        delete_snapshot_map($scfg, $storeid, $m);
    }

    if (is_nvme($scfg)) {
        unlink nvme_wwid_file($storeid, $volname);       # the namespace goes away with the mapping
    } else {
        device_remove_local($info->{vdisk_UID});
    }
    unmap_vdisk($scfg, $storeid, $vname);
    # QoS (mkthrottle, 7.7+) attached to the volume goes with it
    if (my $tid = $info->{throttle_id}) {
        eval { sw_run($scfg, $storeid, 'rmthrottle', [], $tid) };
        warn "storwize: could not remove throttle $tid of '$vname': $@" if $@;
    }
    if ($trash) {
        sw_run($scfg, $storeid, 'chvdisk', [novolumegroup => undef], $vname) if $own_vg;
        sw_run($scfg, $storeid, 'chvdisk', [name => $trash], $vname);
        print "storwize: '$vname' unmapped and moved to the trash as '$trash';"
            . " deleted after " . trash_days($scfg) . " day(s)\n";
    } else {
        sw_run($scfg, $storeid, 'rmvdisk', [], $vname);
    }

    # one volume group per guest: drop it together with its last volume (it stays while
    # snapshots - e.g. Safeguarded ones - still hang on it)
    if (snapmode($scfg) eq 'volumegroup') {
        my (undef, undef, $vmid) = $class->parse_volname($volname);
        my $gvg = guest_vg_name($scfg, $vmid);
        eval {
            my $g = sw_show_opt($scfg, $storeid, 'lsvolumegroup', [], $gvg);
            sw_run($scfg, $storeid, 'rmvolumegroup', [], $gvg)
                if $g && ($g->{volume_count} // 1) == 0;
        };
        warn "storwize: volume group '$gvg' kept: $@" if $@;
    }

    unlink uid_cache_file($storeid, $volname);
    unlink run_dir($storeid) . "/status.json";
    return undef;
}

sub activate_volume {
    my ($class, $storeid, $scfg, $volname, $snapname, $cache, $hints) = @_;

    return $class->snapro_activate($scfg, $storeid, $volname, $snapname) if defined($snapname);

    return nvme_activate($scfg, $storeid, $volname) if is_nvme($scfg);

    my $uid = volume_uid($scfg, $storeid, $volname, 1);
    my $dev = uid_to_path($uid);
    my $wwid = uid_to_wwid($uid);

    if (-b $dev) {
        device_rescan_size($dev, $wwid);
        return 1;
    }

    rescan_hosts($scfg);
    udev_settle();
    multipath_add($wwid);
    udev_settle();

    my $deadline = time() + $DEV_WAIT;
    my $n = 0;
    while (!-b $dev) {
        die "storwize: device $dev did not appear after ${DEV_WAIT}s"
            . " (check zoning/mapping and 'multipath -ll')\n" if time() > $deadline;
        sleep(1);
        if (++$n % 5 == 0) {
            rescan_hosts($scfg);
            multipath_add($wwid);
        }
    }
    return 1;
}

sub deactivate_volume {
    my ($class, $storeid, $scfg, $volname, $snapname, $cache, $hints) = @_;
    snapro_deactivate($scfg, $storeid, $volname, $snapname) if defined($snapname);
    return 1;
}

sub map_volume {
    my ($class, $storeid, $scfg, $volname, $snapname) = @_;
    $class->activate_volume($storeid, $scfg, $volname, $snapname);
    return $class->path($scfg, $volname, $storeid, $snapname);
}

sub unmap_volume { return 1; }

sub volume_size_info {
    my ($class, $scfg, $storeid, $volname, $timeout) = @_;
    my $vname = vdisk_name($scfg, $volname);
    my $info = vdisk_info($scfg, $storeid, $vname)
        or die "storwize: volume '$vname' does not exist\n";
    my $size = int($info->{capacity} // 0);
    my $used = int($info->{used_capacity} // $size);
    return wantarray ? ($size, 'raw', $used, undef) : $size;
}

sub volume_resize {
    my ($class, $scfg, $storeid, $volname, $size, $running) = @_;

    my $vname = vdisk_name($scfg, $volname);
    my $info = vdisk_info($scfg, $storeid, $vname)
        or die "storwize: volume '$vname' does not exist\n";

    die "storwize: cannot resize '$vname' while it has FlashCopy snapshots/maps"
        . " - delete the snapshots first\n" if int($info->{fc_map_count} // 0) > 0;

    my $cur = int($info->{capacity});
    my $new = int(($size + 511) / 512) * 512;
    die "storwize: shrinking is not supported ($cur -> $new bytes)\n" if $new < $cur;
    return $cur if $new == $cur;

    sw_run($scfg, $storeid, 'expandvdisksize', [size => $new - $cur, unit => 'b'], $vname);

    my $uid = $info->{vdisk_UID};
    if (is_nvme($scfg)) {
        nvme_rescan();                       # the namespace picks up the new size on a controller rescan
    } else {
        my $dev = uid_to_path($uid);
        device_rescan_size($dev, uid_to_wwid($uid)) if -b $dev;
    }
    unlink run_dir($storeid) . "/status.json";
    return $new;
}

sub rename_volume {
    my ($class, $scfg, $storeid, $source_volname, $target_vmid, $target_volname) = @_;

    my $src = vdisk_name($scfg, $source_volname);
    my $info = vdisk_info($scfg, $storeid, $src)
        or die "storwize: volume '$src' does not exist\n";
    die "storwize: cannot rename '$src' while it has FlashCopy snapshots\n"
        if int($info->{fc_map_count} // 0) > 0;

    if (!defined($target_volname)) {
        my $existing = $class->list_images($storeid, $scfg, $target_vmid);
        my %used = map { (split(/:/, $_->{volid}, 2))[1] => 1 } @$existing;
        for my $i (0 .. 999) {
            next if $used{"vm-$target_vmid-disk-$i"};
            $target_volname = "vm-$target_vmid-disk-$i";
            last;
        }
    }
    my (undef, undef, $owner) = $class->parse_volname($target_volname);
    die "storwize: target '$target_volname' does not belong to VM $target_vmid\n"
        if $owner != $target_vmid;
    my $dst = vdisk_name($scfg, $target_volname);
    die "storwize: volume '$dst' already exists\n" if vdisk_info($scfg, $storeid, $dst);

    sw_run($scfg, $storeid, 'chvdisk', [name => $dst], $src);
    unlink uid_cache_file($storeid, $source_volname);
    return "$storeid:$target_volname";
}

sub create_base { die "storwize: templates are not supported\n"; }

sub clone_image { die "storwize: linked clones are not supported (use a full clone)\n"; }

sub volume_has_feature {
    my ($class, $scfg, $feature, $storeid, $volname, $snapname, $running, $opts) = @_;
    my $features = {
        snapshot   => { current => 1 },
        copy       => { current => 1 },
        rename     => { current => 1 },
        sparseinit => { current => ($scfg->{'sw-thin'} // 1) ? 1 : 0 },
    };
    # 8.5+ volume group snapshots can be read through a thinclone (full clone from a snapshot)
    $features->{copy}{snap} = 1 if snapmode($scfg) eq 'volumegroup' && !is_nvme($scfg);
    my $key = defined($snapname) ? 'snap' : 'current';
    return $features->{$feature}->{$key} ? 1 : undef;
}

# ---------------------------------------------------------------------------
# Snapshots: FlashCopy, one map per disk
# ---------------------------------------------------------------------------

sub wait_fcmap_stopped {
    my ($scfg, $storeid, $id) = @_;
    my $deadline = time() + $FC_STOP_WAIT;
    while (1) {
        my $m = sw_show_opt($scfg, $storeid, 'lsfcmap', [], $id) // return 1;
        my $st = $m->{status} // '';
        return 1 if $st eq 'stopped' || $st eq 'idle_or_copied';
        die "storwize: FlashCopy map $id still '$st' after ${FC_STOP_WAIT}s\n" if time() > $deadline;
        sleep(2);
    }
}

# Rollback (restore) maps created with -autodelete stay 'copying' at 100% for a
# few seconds before the array removes them. Wait for those; refuse (die) while
# a rollback is really still copying.
sub wait_rollbacks_done {
    my ($scfg, $storeid, $filter, $what) = @_;
    my $deadline = time() + 120;
    while (1) {
        my $maps = sw_list($scfg, $storeid, 'lsfcmap', [filtervalue => $filter]);
        my @active = grep { ($_->{status} // '') !~ m/^(idle_or_copied|stopped)$/ } @$maps;
        return 1 if !@active;
        my ($slow) = grep { int($_->{progress} // 0) < 100 } @active;
        die "storwize: $what is still copying ($slow->{progress}%) - wait and retry\n" if $slow;
        die "storwize: $what did not finish (map $active[0]->{id} '$active[0]->{status}')\n"
            if time() > $deadline;
        sleep(2);
    }
}

sub delete_snapshot_map {
    my ($scfg, $storeid, $map) = @_;

    my $target = $map->{target_vdisk_name} // '';
    die "storwize: refusing to delete FlashCopy map $map->{id}: target '$target'"
        . " is not a snapshot managed by this storage\n" if !is_own_snap_target($scfg, $target);

    # rollback maps read FROM this snapshot (source = snapshot target volume)
    wait_rollbacks_done($scfg, $storeid, "source_vdisk_name=$target",
        "a rollback from snapshot '$target'");

    my $group = $map->{group_name} // '';
    die "storwize: FlashCopy map $map->{id} belongs to consistency group '$group',"
        . " which is not managed by this storage\n" if $group ne '' && !is_own_cg($scfg, $group);

    my $st = $map->{status} // '';
    if ($st ne 'stopped' && $st ne 'idle_or_copied') {
        if ($group ne '') {
            # maps of a consistency group can only be stopped together; the whole
            # snapshot (every disk of the guest) is being deleted anyway
            my $g = sw_show_opt($scfg, $storeid, 'lsfcconsistgrp', [], $group);
            sw_run($scfg, $storeid, 'stopfcconsistgrp', [], $group)
                if $g && ($g->{status} // '') !~ m/^(stopped|idle_or_copied|stopping|empty)$/;
        } else {
            sw_run($scfg, $storeid, 'stopfcmap', [], $map->{id});
        }
        wait_fcmap_stopped($scfg, $storeid, $map->{id});
    }
    sw_run($scfg, $storeid, 'rmfcmap', [force => undef], $map->{id});
    sw_run($scfg, $storeid, 'rmvdisk', [], $target);

    if ($group ne '') {
        my $left = sw_list($scfg, $storeid, 'lsfcmap', [filtervalue => "group_name=$group"]);
        sw_run($scfg, $storeid, 'rmfcconsistgrp', [force => undef], $group) if !@$left;
    }
}

# ---- consistency between the disks of one guest -------------------------

sub snapmode { my ($scfg) = @_; return $scfg->{'sw-snapmode'} // 'flashcopy'; }

sub cg_name {
    my ($scfg, $vmid, $snap) = @_;
    my $n = sw_prefix($scfg) . "-vm-${vmid}_s_$snap";
    die "storwize: consistency group name '$n' longer than $MAX_NAME characters\n" if length($n) > $MAX_NAME;
    return $n;
}

sub is_own_cg {
    my ($scfg, $name) = @_;
    my $p = quotemeta(sw_prefix($scfg));
    return $name =~ m/^${p}-vm-\d+_s_[A-Za-z][A-Za-z0-9_-]*$/;
}

sub guest_vg_name {
    my ($scfg, $vmid) = @_;
    return sw_prefix($scfg) . "-vm-$vmid";
}

# Disks of the guest (current config only) that live on this storage.
sub guest_disks {
    my ($storeid, $vmid) = @_;
    for my $f ("/etc/pve/qemu-server/$vmid.conf", "/etc/pve/lxc/$vmid.conf") {
        next if !-f $f;
        open(my $fh, '<', $f) or next;
        my @vols;
        while (my $l = <$fh>) {
            last if $l =~ m/^\[/;                        # snapshot sections follow
            next if $l !~ m/^(?:(?:scsi|sata|ide|virtio|efidisk|tpmstate)\d+|rootfs|mp\d+):\s*(\S+)/;
            my $val = $1;
            next if $val =~ m/(?:^|,)media=cdrom(?:,|$)/;
            next if $val !~ m/^\Q$storeid\E:(vm-\d+-[A-Za-z0-9][A-Za-z0-9_-]*)(?:,|$)/;
            push @vols, $1;
        }
        close($fh);
        return grep { !m/^vm-\d+-state-/ } @vols;
    }
    return ();
}

# Snapshot of several disks at the same instant: one FlashCopy map per disk,
# all in one consistency group started with a single startfcconsistgrp.
sub snapshot_group {
    my ($scfg, $storeid, $vmid, $snap, $volnames) = @_;

    my $cg = cg_name($scfg, $vmid, $snap);
    my (@targets, @maps, $cg_made, @src);
    for my $v (@$volnames) {
        my $vname = vdisk_name($scfg, $v);
        my $tname = snap_vdisk_name($scfg, $v, $snap);
        my $info = vdisk_info($scfg, $storeid, $vname)
            or die "storwize: volume '$vname' does not exist\n";
        die "storwize: snapshot volume '$tname' already exists\n" if vdisk_info($scfg, $storeid, $tname);
        push @src, [$vname, $tname, $info];
    }
    die "storwize: consistency group '$cg' already exists\n"
        if sw_show_opt($scfg, $storeid, 'lsfcconsistgrp', [], $cg);

    eval {
        sw_run($scfg, $storeid, 'mkfcconsistgrp', [name => $cg]);
        $cg_made = 1;
        for my $x (@src) {
            my ($vname, $tname, $info) = @$x;
            sw_run($scfg, $storeid, 'mkvdisk', [
                name => $tname, mdiskgrp => $scfg->{'sw-snappool'} // $scfg->{'sw-pool'},
                iogrp => $info->{IO_group_name} // ($scfg->{'sw-iogrp'} // 'io_grp0'),
                size => int($info->{capacity}), unit => 'b', rsize => '0%', autoexpand => undef,
            ]);
            push @targets, $tname;
            my $r = sw_run($scfg, $storeid, 'mkfcmap', [
                source => $vname, target => $tname, copyrate => 0, cleanrate => 50, consistgrp => $cg,
            ]);
            push @maps, $r->{id} // die "storwize: mkfcmap returned no id\n";
        }
        sw_run($scfg, $storeid, 'startfcconsistgrp', [prep => undef], $cg);
    };
    if (my $err = $@) {
        eval { sw_run($scfg, $storeid, 'stopfcconsistgrp', [], $cg) } if $cg_made;
        eval { sw_run($scfg, $storeid, 'rmfcmap', [force => undef], $_) } for @maps;
        eval { sw_run($scfg, $storeid, 'rmvdisk', [], $_) } for @targets;
        eval { sw_run($scfg, $storeid, 'rmfcconsistgrp', [force => undef], $cg) } if $cg_made;
        die $err;
    }
    return 1;
}

# ---- 8.5.1+: snapshot of the guest's volume group (EXPERIMENTAL) -----------
# Command names follow the IBM white paper (addsnapshot / rmsnapshot /
# restorefromsnapshot); not validated on a real 8.x system yet.

sub vg_snapshot_exists {
    my ($scfg, $storeid, $vg, $snap) = @_;
    # 8.5.1+: the concise view lists every snapshot; filter by group (IBM Ansible module:
    # lsvolumegroupsnapshot -snapshot <name> -volumegroup <vg> / -filtervalue)
    my $rows = eval {
        sw_list($scfg, $storeid, 'lsvolumegroupsnapshot', [filtervalue => "volume_group_name=$vg"]);
    } // [];
    return scalar grep {
        ($_->{volume_group_name} // $vg) eq $vg && ($_->{name} // $_->{snapshot_name} // '') eq $snap
    } @$rows;
}

sub find_snapshot_map {
    my ($scfg, $storeid, $vname, $tname) = @_;
    my $maps = sw_list($scfg, $storeid, 'lsfcmap', [filtervalue => "target_vdisk_name=$tname"]);
    my ($m) = grep { ($_->{source_vdisk_name} // '') eq $vname } @$maps;
    return $m;
}

sub volume_snapshot {
    my ($class, $scfg, $storeid, $volname, $snap) = @_;

    my (undef, undef, $vmid) = $class->parse_volname($volname);

    if (snapmode($scfg) eq 'volumegroup') {
        my $vg = guest_vg_name($scfg, $vmid);
        sw_run($scfg, $storeid, 'addsnapshot', [volumegroup => $vg, name => $snap])
            if !vg_snapshot_exists($scfg, $storeid, $vg, $snap);
        return undef;
    }

    if ($scfg->{'sw-consistency'} // 1) {
        my @disks = guest_disks($storeid, $vmid);
        if (@disks > 1 && grep { $_ eq $volname } @disks) {
            my $vname = vdisk_name($scfg, $volname);
            my $tname = snap_vdisk_name($scfg, $volname, $snap);
            # the first disk of the guest already created the whole group
            return undef if find_snapshot_map($scfg, $storeid, $vname, $tname);
            snapshot_group($scfg, $storeid, $vmid, $snap, \@disks);
            unlink run_dir($storeid) . "/status.json";
            return undef;
        }
    }

    my $vname = vdisk_name($scfg, $volname);
    my $tname = snap_vdisk_name($scfg, $volname, $snap);
    my $src = vdisk_info($scfg, $storeid, $vname)
        or die "storwize: volume '$vname' does not exist\n";
    die "storwize: snapshot volume '$tname' already exists\n" if vdisk_info($scfg, $storeid, $tname);

    sw_run($scfg, $storeid, 'mkvdisk', [
        name => $tname,
        mdiskgrp => $scfg->{'sw-snappool'} // $scfg->{'sw-pool'},
        iogrp => $src->{IO_group_name} // ($scfg->{'sw-iogrp'} // 'io_grp0'),
        size => int($src->{capacity}),
        unit => 'b',
        rsize => '0%',
        autoexpand => undef,
    ]);

    my $map_id;
    eval {
        my $r = sw_run($scfg, $storeid, 'mkfcmap', [
            source => $vname, target => $tname, copyrate => 0, cleanrate => 50,
        ]);
        $map_id = $r->{id} // die "storwize: mkfcmap returned no id\n";
        sw_run($scfg, $storeid, 'startfcmap', [prep => undef], $map_id);
    };
    if (my $err = $@) {
        eval { sw_run($scfg, $storeid, 'rmfcmap', [force => undef], $map_id) } if defined($map_id);
        eval { sw_run($scfg, $storeid, 'rmvdisk', [], $tname) };
        die $err;
    }
    unlink run_dir($storeid) . "/status.json";
    return undef;
}

sub volume_rollback_is_possible {
    my ($class, $scfg, $storeid, $volname, $snap, $blockers) = @_;

    if (snapmode($scfg) eq 'volumegroup') {
        my (undef, undef, $vmid) = $class->parse_volname($volname);
        die "storwize: snapshot '$snap' not found in volume group\n"
            if !vg_snapshot_exists($scfg, $storeid, guest_vg_name($scfg, $vmid), $snap);
        return 1;
    }

    my $vname = vdisk_name($scfg, $volname);
    my $tname = snap_vdisk_name($scfg, $volname, $snap);
    find_snapshot_map($scfg, $storeid, $vname, $tname)
        or die "storwize: snapshot '$snap' of '$volname' not found on the storage\n";

    wait_rollbacks_done($scfg, $storeid, "target_vdisk_name=$vname",
        "a previous rollback into '$vname'");
    return 1;
}

sub volume_snapshot_rollback {
    my ($class, $scfg, $storeid, $volname, $snap) = @_;

    $class->volume_rollback_is_possible($scfg, $storeid, $volname, $snap);

    if (snapmode($scfg) eq 'volumegroup') {
        # one restore brings back every volume of the group; later disks of the
        # same rollback find the marker and skip
        my (undef, undef, $vmid) = $class->parse_volname($volname);
        my $marker = run_dir($storeid) . "/restore-$vmid-$snap";
        if (!-f $marker || time() - (stat($marker))[9] > 300) {
            sw_run($scfg, $storeid, 'restorefromsnapshot',
                [snapshot => $snap, volumegroup => guest_vg_name($scfg, $vmid)]);
            file_set_contents($marker, time() . "\n", 0600);
        }
        my $uid = volume_uid($scfg, $storeid, $volname);
        my $dev = volume_dev($scfg, $storeid, $volname, $uid);
        eval { run_command(['/usr/bin/blockdev', '--flushbufs', $dev], noerr => 1) } if -b $dev;
        return undef;
    }

    my $vname = vdisk_name($scfg, $volname);
    my $tname = snap_vdisk_name($scfg, $volname, $snap);

    my $r = sw_run($scfg, $storeid, 'mkfcmap', [
        source => $tname, target => $vname, copyrate => 100, cleanrate => 50, autodelete => undef,
    ]);
    my $id = $r->{id} // die "storwize: mkfcmap (restore) returned no id\n";
    eval { sw_run($scfg, $storeid, 'startfcmap', [prep => undef, restore => undef], $id) };
    if (my $err = $@) {
        eval { sw_run($scfg, $storeid, 'rmfcmap', [force => undef], $id) };
        die $err;
    }

    my $uid = volume_uid($scfg, $storeid, $volname);
    my $dev = volume_dev($scfg, $storeid, $volname, $uid);
    if (-b $dev) {
        eval { run_command(['/usr/bin/blockdev', '--flushbufs', $dev], noerr => 1) };
    }
    return undef;
}

sub volume_snapshot_delete {
    my ($class, $scfg, $storeid, $volname, $snap, $running) = @_;

    if (snapmode($scfg) eq 'volumegroup') {
        my (undef, undef, $vmid) = $class->parse_volname($volname);
        my $vg = guest_vg_name($scfg, $vmid);
        # read access left behind by a clone from this snapshot (Proxmox does not deactivate the
        # source of a clone while the VM runs)
        eval { snapro_deactivate($scfg, $storeid, $volname, $snap) };
        warn "storwize: snapshot access of '$volname' at '$snap' kept: $@" if $@;
        sw_run($scfg, $storeid, 'rmsnapshot', [snapshot => $snap, volumegroup => $vg])
            if vg_snapshot_exists($scfg, $storeid, $vg, $snap);
        return undef;
    }

    my $vname = vdisk_name($scfg, $volname);
    my $tname = snap_vdisk_name($scfg, $volname, $snap);
    my $m = find_snapshot_map($scfg, $storeid, $vname, $tname);
    if (!$m) {
        if (vdisk_info($scfg, $storeid, $tname)) {
            die "storwize: '$tname' exists without its FlashCopy map - remove it manually\n";
        }
        warn "storwize: snapshot '$snap' of '$volname' not found - nothing to delete\n";
        return undef;
    }
    delete_snapshot_map($scfg, $storeid, $m);
    unlink run_dir($storeid) . "/status.json";
    return undef;
}

sub volume_snapshot_info {
    my ($class, $scfg, $storeid, $volname) = @_;
    my $vname = vdisk_name($scfg, $volname);
    my $maps = snapshot_maps($scfg, $storeid, $vname);
    my $res = {};
    my $order = 0;
    for my $m (sort { ($a->{start_time} // '') cmp ($b->{start_time} // '') } @$maps) {
        my $t = $m->{target_vdisk_name} // next;
        next if !is_own_snap_target($scfg, $t);
        my ($snap) = $t =~ m/_s_([A-Za-z][A-Za-z0-9_-]*)$/;
        $res->{$snap} = { order => ++$order, id => $m->{id}, status => $m->{status} };
    }
    $res->{current} = { order => ++$order };
    return $res;
}

1;
