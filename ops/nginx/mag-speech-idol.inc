# Included by existing MAG private HTTPS vhost through mag-speech*.inc.
# The server-level auth_request /_mag_whois is inherited by this location.
# Direct public access remains blocked by the existing Tailnet ACL.
location ^~ /api/idol/ {
    client_max_body_size 20m;
    proxy_pass http://127.0.0.1:8793;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Connection "";
    proxy_set_header X-Forwarded-For "";
    proxy_set_header X-Mag-Remote "";
    # Never expose the localhost-only 3D deletion capability to browsers.
    proxy_set_header X-Mag-Idol-Internal "";
    proxy_connect_timeout 3s;
    proxy_read_timeout 60s;
    proxy_request_buffering on;
    proxy_buffering off;
    add_header Cache-Control "no-store" always;
    add_header X-Content-Type-Options "nosniff" always;
}
