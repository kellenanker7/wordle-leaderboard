# URL Shortener

```bash
npm install
sls create-cert
poetry export --without-hashes --f requirements.txt -o requirements.txt --with-credentials
sls deploy --stage live
```

## Metrics
```bash
# Service status
curl 'https://kell.link/api/status'

# Get all items created within last 10 days
curl -H "x-kellink-token: let-me-in" 'https://kell.link/api/search?days=10'

# Get all clicks, grouped by suid and long_url
curl -H "x-kellink-token: let-me-in" 'https://kell.link/api/clicks'

# Get all clicks, for specific suid
curl -H "x-kellink-token: let-me-in" 'https://kell.link/api/clicks?suid=rLFlJY'

# Get all clicks, for specific long_url
curl -H "x-kellink-token: let-me-in" 'https://kell.link/api/clicks?long_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ'
```
