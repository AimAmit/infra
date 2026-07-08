# WARP Proxy

This namespace provides a centralized Cloudflare WARP proxy service for the cluster. It allows internal workloads (e.g., `yt-dlp-server`, `quantlab`) to route their outbound HTTP/HTTPS traffic through WARP to avoid IP-based rate limiting or blocking.

## Usage

To utilize the proxy from other namespaces, set the proxy environment variables in your workload configuration to point to the `warp-proxy-svc`:

```yaml
env:
  - name: HTTP_PROXY
    value: "socks5h://warp-proxy-svc.warp-proxy.svc.cluster.local:1080"
  - name: HTTPS_PROXY
    value: "socks5h://warp-proxy-svc.warp-proxy.svc.cluster.local:1080"
```
