# Remote Access

CodePlane can be accessed from any device — including your phone — via Dev Tunnels, Microsoft's secure tunneling service.

## Enabling Remote Access

CodePlane supports two tunnel providers for remote access.

### Dev Tunnels (default)

```bash
cpl up --remote
```

Or with a password:

```bash
cpl up --remote --password your-secret
```

The server will print a tunnel URL like:

```
https://abc123.devtunnels.ms
```

### Cloudflare Tunnels

For a stable, user-managed hostname:

```bash
cpl up --remote --provider cloudflare
```

Prerequisites:

1. Create a named tunnel at [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)
2. Route a public hostname to `localhost:8080`
3. Set environment variables:

```bash
export CPL_CLOUDFLARE_TUNNEL_TOKEN=your-token
export CPL_CLOUDFLARE_HOSTNAME=codeplane.yourdomain.com
```

Open your configured hostname on any device to access the UI.

## Security

| Feature | Details |
|---------|---------|
| **HTTPS** | All tunnel traffic is encrypted end-to-end |
| **Password protection** | Set `CPL_DEVTUNNEL_PASSWORD` or use `--password` (Dev Tunnels) |
| **Cloudflare auth** | Managed via Cloudflare Zero Trust access policies |
| **Localhost trust** | Direct access on `localhost` requires no password |

!!! warning "Without a Password"
    If you don't set a password, anyone with the tunnel URL can access CodePlane. Always set a password when using remote access on shared networks.

## Mobile Experience

The UI is fully responsive. On mobile devices:

<div class="screenshot-mobile" markdown>
![Mobile Dashboard](../images/screenshots/mobile/mobile-dashboard.png)
</div>

- **Dashboard** switches to a tab-based list view
- **Terminal** drawer maximizes to full screen
- **Forms** use compact layouts
- **Voice input** works via the mobile browser's microphone API

## Use Cases

- **Monitor from your phone** — Watch jobs run while away from your desk
- **Approve actions** — Handle approval requests from anywhere
- **Quick interventions** — Cancel or send messages to running agents
- **Demo** — Share the URL to show CodePlane in action
