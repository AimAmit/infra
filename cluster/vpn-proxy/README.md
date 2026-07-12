# VPN Proxy (Proton US exit)

> Backed by **Proton VPN**. (Originally prototyped on VPNBook, whose free
> shared-key WireGuard was too unstable to use.)

WireGuard client (via [gluetun](https://github.com/qdm12/gluetun)) that tunnels
outbound traffic through a **Proton VPN US exit** (`US-FREE#66`) and exposes a
local HTTP proxy on port `8888`. Pure egress — nothing exposed to the public
internet.

Use it to route headless-browser / `yt-dlp` traffic through a US IP, e.g. for
YouTube. Killswitch is on by default: if the tunnel drops, traffic is blocked
(no leak to the node's real IP).

## Secret setup (do this once, before ArgoCD syncs)

The WireGuard private key is **not** stored in git. Create it manually from the
Proton VPN `.conf` file's `PrivateKey`:

```bash
kubectl create namespace vpn-proxy   # ok if it already exists
kubectl -n vpn-proxy create secret generic proton-wg \
  --from-literal=WIREGUARD_PRIVATE_KEY='<PrivateKey from the Proton .conf>'
```

`secret.example.yaml` is a template only — do not fill it in or commit it.

The non-sensitive peer values (public key, endpoint, address) live directly in
`deployment.yaml`. If you regenerate the Proton config, update those + rerun the
secret command above.

## Usage from cluster workloads

Point any namespace's workload at the proxy:

```yaml
env:
  - name: HTTP_PROXY
    value: "http://vpn-proxy-svc.vpn-proxy.svc.cluster.local:8888"
  - name: HTTPS_PROXY
    value: "http://vpn-proxy-svc.vpn-proxy.svc.cluster.local:8888"
```

Fallback: if the OVH exit gets blocked, switch these to the existing
`warp-proxy-svc` (Cloudflare) — see `../warp-proxy/README.md`.

## Usage from local (over Tailscale)

The Service is published to the tailnet by the Tailscale operator as a device
named `vpn-proxy`. With MagicDNS on, reach it by short name; otherwise use the
full FQDN `vpn-proxy.<your-tailnet>.ts.net`. From any device on your tailnet:

```bash
curl --proxy http://vpn-proxy:8888 https://ipinfo.io/country                  # MagicDNS
curl --proxy http://vpn-proxy.<your-tailnet>.ts.net:8888 https://ipinfo.io/country  # FQDN
```

Or set it as the system/browser HTTP proxy to route local traffic through the
US exit. Reachable only over the tailnet — not public.

## Verify exit location

```bash
kubectl -n vpn-proxy exec deploy/vpn-proxy -- \
  wget -qO- --proxy off https://ipinfo.io/country
```

If the tunnel stops connecting, regenerate the Proton WireGuard config and
update the secret + peer values in `deployment.yaml`.
