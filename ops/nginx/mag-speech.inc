# Optional include INSIDE the existing nginx HTTPS server block; only nginx.
# Avoids loading 122 MB weights into the native magroom C++ HTTP process.
# All URLs are self-hosted under /opt/mag-stack/current/web/static.
location ~ "^/static/speech-[0-9a-f]{12}/[A-Za-z0-9_.-]+$" {
    root /opt/mag-stack/current/web;
    types {
        text/javascript js mjs;
        application/wasm wasm;
        application/json json;
        application/octet-stream ort bin;
    }
    default_type application/octet-stream;
    add_header Cache-Control "public, max-age=31536000, immutable" always;
    add_header Cross-Origin-Resource-Policy "same-origin" always;
    # Module Workers inherit isolation only when their entry script has COEP.
    add_header Cross-Origin-Embedder-Policy "require-corp" always;
    access_log off;
}
# Version selector must be fresh after deploy.
location = /static/speech-config.json {
    root /opt/mag-stack/current/web;
    default_type application/json;
    add_header Cache-Control "no-store" always;
}
# Keep existing app / reverse proxy. Ensure the HTML gets:
# Cross-Origin-Opener-Policy: same-origin
# Cross-Origin-Embedder-Policy: require-corp
# These are also emitted by magroom's main HTML route.
