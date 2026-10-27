# $remote_addr is the PROXY v2 client (listen proxy_protocol).
# Include this once per http{}. Room, pages and bots all use $mag_tailnet_ok.
# Live: deploy-nginx.sh stage copies this file to /etc/nginx/snippets/
# before nginx -t, while /opt/mag-stack/current is still the old release.
geo $remote_addr $mag_tailnet_ok {
    default 0;
    100.64.0.0/10 1;
    fd7a:115c:a1e0::/48 1;
    127.0.0.1 1;
    ::1 1;
}
