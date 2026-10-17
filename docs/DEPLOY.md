# Deploying

## Local

```bash
make data prepare infra index      # once
make up                            # api :8120, ui :4310
make up-obs                        # prometheus :9107, alertmanager :9108, grafana :3008 (admin / change-me)
make load                          # k6 at 10, 25 and 50 requests/s, 60 s per scenario -> reports/k6-r*.json
make down
```

`docker compose --profile jobs run --rm indexer` rebuilds the index from inside the image, the way the VM
does it.

## Oracle Cloud always-free VM with Terraform

What it creates: a VCN with a public subnet and an internet gateway, a security list (22 from your
address, 80 from anywhere), and a `VM.Standard.A1.Flex` instance with 4 OCPUs and 24 GB on Ubuntu 24.04.
All of it is inside the always-free limits, so the monthly cost is zero while it stays there. The A1
shape is popular; if the region reports "out of host capacity", try another availability domain or
region (`terraform apply -var region=us-phoenix-1`).

1. Free account at https://www.oracle.com/cloud/free/ (a card is used for identity only). Then Profile,
   API keys, Add API key: download the private key to `~/.oci/oci_api_key.pem` and note the tenancy OCID,
   user OCID, fingerprint and region it shows.
2. `cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars` and fill it in,
   including an SSH public key, your address as `admin_cidr`, your GitHub user as `ghcr_owner`, and a
   long random `admin_key`.
3. `gh auth login` with a token that can write packages; `make prepare index` so the embeddings exist
   locally (they are uploaded, not recomputed).
4. `make deploy`: multi-arch images to GHCR, `terraform apply`, wait for cloud-init, `rsync` the prepared
   data and reports, run the indexer on the VM (a few minutes for 200,000 documents), start the API and
   UI.
5. Open `http://<public_ip>`; `http://<public_ip>/api/health` for the API. Only port 80 is reachable;
   OpenSearch and Redis are not published on the VM.
6. `make teardown` destroys everything.

The GHCR images are private by default; cloud-init pulls them anonymously, so either make the two
packages public in the GitHub UI or add a `docker login` with a read-only token to the cloud-init
`runcmd` before running the indexer.

## CI deploy

Not wired: it needs the OCI API key and the SSH key as GitHub secrets. `scripts/deploy.sh` is the whole
procedure and would run from a `workflow_dispatch` job once those secrets exist.
