#!/bin/sh
# Runs as root before the panel starts. Creates the self-signed certificate if the
# container has none yet (first boot of the appliance image) and, while the panel is
# not configured, generates the installation code and shows it on the container
# console (/etc/issue).
D=/var/lib/swpanel
# on a first boot the address may still be on its way from DHCP
for i in $(seq 1 45); do
    ip=$(hostname -I | awk '{print $1}')
    [ -n "$ip" ] && break
    sleep 1
done
if [ ! -s /etc/swpanel/tls.key ] || [ ! -s /etc/swpanel/tls.crt ]; then
    openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=$(hostname)" \
        -addext "subjectAltName=DNS:$(hostname)${ip:+,IP:$ip}" -keyout /etc/swpanel/tls.key -out /etc/swpanel/tls.crt 2>/dev/null
    chgrp swpanel /etc/swpanel/tls.key /etc/swpanel/tls.crt; chmod 640 /etc/swpanel/tls.key
fi
install -d -m 0700 -o swpanel -g swpanel $D $D/keys $D/update
if [ -s $D/config.json ] || [ -s /etc/swpanel/config.json ]; then
    rm -f $D/setup-code
    [ -f /etc/issue.swpanel-orig ] && cp /etc/issue.swpanel-orig /etc/issue
    exit 0
fi
if [ ! -s $D/setup-code ]; then
    c=$(tr -dc 'A-HJ-NP-Z2-9' < /dev/urandom 2>/dev/null | head -c 8)
    echo "$(echo $c | cut -c1-4)-$(echo $c | cut -c5-8)" > $D/setup-code
    chown swpanel:swpanel $D/setup-code; chmod 600 $D/setup-code
fi
[ -f /etc/issue.swpanel-orig ] || cp /etc/issue /etc/issue.swpanel-orig 2>/dev/null
printf 'IBM Storage Plugin Proxmox (beta)\n\n  Panel:             https://%s/\n  Installation code: %s\n\n' "${ip:-<container address>}" "$(cat $D/setup-code)" > /etc/issue
exit 0
