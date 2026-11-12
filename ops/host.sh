# shellcheck shell=bash
# The host's identity and owner for every MAG script: source it.
#   source "$(dirname "$0")/../host.sh"     (from ops/<area>/)
# Sets MAG_OWNER, MAG_OWNER_HOME, MAG_DOMAIN, MAG_BOTS_ZONE, MAG_TAILNET_IP,
# MAG_TS_NAME, MAG_CONTACT, MAG_IDOL_BASE, MAG_MODELS_HOME from ops/host.env,
# then /etc/mag-stack/host.env, then the environment. mag_render maps the
# reference host's values in a repository file (units, nginx, models.json,
# app.ini) to this host's: mag_render IN > OUT.
_mag_host_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
_mag_host_load() { # file: KEY=VALUE lines, only MAG_* keys, the environment wins
  local line key value
  [[ -r $1 ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^(MAG_[A-Z_]+)=(.*)$ ]] || continue
    key=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
    value=${value%\"}; value=${value#\"}
    [[ -n ${!key+x} && ${_mag_host_from_env[$key]:-} == 1 ]] && continue
    printf -v "$key" '%s' "$value"
  done < "$1"
}
declare -A _mag_host_from_env=()
for _k in MAG_OWNER MAG_DOMAIN MAG_BOTS_ZONE MAG_TAILNET_IP MAG_TS_NAME MAG_CONTACT; do
  [[ -n ${!_k:-} ]] && _mag_host_from_env[$_k]=1
done
_mag_host_load "$_mag_host_dir/host.env"
_mag_host_load "${MAG_HOST_ENV:-/etc/mag-stack/host.env}"
unset _k
MAG_OWNER_HOME=$(getent passwd "$MAG_OWNER" | cut -d: -f6)
MAG_OWNER_HOME=${MAG_OWNER_HOME:-/home/$MAG_OWNER}
if [[ -z $MAG_TAILNET_IP ]] && command -v tailscale >/dev/null; then
  MAG_TAILNET_IP=$(tailscale ip -4 2>/dev/null | head -1 || true)
fi
if [[ -z $MAG_TS_NAME ]] && command -v tailscale >/dev/null && command -v jq >/dev/null; then
  MAG_TS_NAME=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // "" | rtrimstr(".")' || true)
fi
MAG_IDOL_BASE=${MAG_IDOL_BASE:-$MAG_OWNER_HOME/idol-mi50}
MAG_MODELS_HOME=${MAG_MODELS_HOME:-$MAG_OWNER_HOME/models}
export MAG_OWNER MAG_OWNER_HOME MAG_DOMAIN MAG_BOTS_ZONE MAG_TAILNET_IP MAG_TS_NAME MAG_CONTACT MAG_IDOL_BASE MAG_MODELS_HOME
# The reference host's values, as they stand in the repository's files.
MAG_REF_OWNER=chris MAG_REF_DOMAIN=mag.stardenburg.de MAG_REF_ZONE=bot.stardenburg.de
MAG_REF_IP=100.123.114.16 MAG_REF_TS=mag.tail3e02ae.ts.net
mag_render() { # file -> stdout, with this host's identity
  local ip=${MAG_TAILNET_IP:-$MAG_REF_IP} ts=${MAG_TS_NAME:-$MAG_REF_TS}
  sed -e "s#/home/$MAG_REF_OWNER\\b#$MAG_OWNER_HOME#g" \
      -e "s#^\\(User\\|Group\\)=$MAG_REF_OWNER\$#\\1=$MAG_OWNER#" \
      -e "s# $MAG_REF_OWNER $MAG_REF_OWNER # $MAG_OWNER $MAG_OWNER #g" \
      -e "s#${MAG_REF_IP//./\\.}#$ip#g" \
      -e "s#${MAG_REF_TS//./\\.}#$ts#g" \
      -e "s#${MAG_REF_ZONE//./\\.}#$MAG_BOTS_ZONE#g" \
      -e "s#${MAG_REF_DOMAIN//./\\.}#$MAG_DOMAIN#g" "$1"
}
