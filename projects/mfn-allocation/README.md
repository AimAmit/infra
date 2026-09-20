# MFN allocation service

Argo CD deploys the private `multi-factor-nifty` image and exposes its HTTP
service only to the tailnet at `https://mfn-allocation.<tailnet>.ts.net`.

Outbound traffic permits cluster DNS and public HTTPS for Kite authentication,
broker APIs and Yahoo prices. Private-address HTTPS remains blocked. Inbound
access stays restricted to the Tailscale namespace.

The namespace needs a `ghcr-secret` registry credential with `read:packages`.
Runtime Parquet state lives on the `mfn-data` PVC. The rebalance endpoint needs
the latest completed rank snapshot at:

```text
/app/data/output/mqv_core_latest_YYYYMMDD/allocation_report/closing_score_diagnostic.parquet
```

Its optional Gold state is read from `completed_close_gold_signals.csv` beside
that Parquet file. Neither file belongs in this Git repository.
