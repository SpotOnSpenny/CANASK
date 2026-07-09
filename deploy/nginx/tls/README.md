# nginx origin TLS material

Drop the following files here before running `make prod-up`. They are **secrets** and are gitignored
(everything in this directory except this README).

| File | What it is | Where to get it |
|---|---|---|
| `origin.pem` | Cloudflare **Origin Certificate** (public cert) | Cloudflare dashboard → SSL/TLS → Origin Server → *Create Certificate*. Save the certificate as `origin.pem`. |
| `origin.key` | Private key for the origin certificate | Shown once alongside the certificate above. Save as `origin.key` (keep it secret). |
| `cloudflare-origin-pull-ca.pem` | Cloudflare **Authenticated Origin Pull** CA | https://developers.cloudflare.com/ssl/origin-configuration/authenticated-origin-pull/ (the "Cloudflare origin pull" CA PEM). |

Then, in the Cloudflare dashboard:

1. **SSL/TLS → Overview**: set the mode to **Full (strict)** (it is currently *Flexible*).
2. **SSL/TLS → Origin Server**: enable **Authenticated Origin Pulls** (zone-level).
3. Firewall the server so ports 80/443 accept traffic **only from Cloudflare IP ranges**
   (https://www.cloudflare.com/ips/) — belt-and-suspenders with the mTLS check.

`deploy/nginx/canask.conf` references these files at `/etc/nginx/tls/` (mounted read-only by the
`nginx` service in `docker-compose.prod.yml`).
