package PVE::Storage::Custom::FlashSystemPlugin;

# IBM FlashSystem (Storage Virtualize 8.x / 9.x) as its own Proxmox storage type.
#
# Same code as the Storwize plugin: the two families run IBM Storage Virtualize and
# every feature is detected from the code level. The separate type only makes the
# storage list in Proxmox say what the array is:
#
#     storwize     IBM Storwize (V3700/V5000/V7000 ...) and SAN Volume Controller
#     flashsystem  IBM FlashSystem (5x00/7x00/9x00 ...)
#
# The sw-* options are declared once, by the Storwize plugin, and shared.

use strict;
use warnings;

use PVE::Storage::Plugin;
use PVE::Storage::Custom::StorwizePlugin;

use base qw(PVE::Storage::Custom::StorwizePlugin);

push @PVE::Storage::Plugin::SHARED_STORAGE, 'flashsystem'
    if !grep { $_ eq 'flashsystem' } @PVE::Storage::Plugin::SHARED_STORAGE;

sub type { return 'flashsystem'; }

# already registered by PVE::Storage::Custom::StorwizePlugin
sub properties { return {}; }

1;
