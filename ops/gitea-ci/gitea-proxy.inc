proxy_pass http://127.0.0.1:3000;
proxy_http_version 1.1;
proxy_set_header Host mag.tail3e02ae.ts.net;
proxy_set_header X-Forwarded-Proto https;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $mag_git_upgrade;
# Never trust externally supplied authentication/owner headers.
proxy_set_header X-WEBAUTH-USER "";
proxy_set_header X-WEBAUTH-EMAIL "";
proxy_set_header X-Tailscale-User "";
proxy_set_header X-Mag-Auth-Proof "";
proxy_buffering off;
proxy_read_timeout 300s;
