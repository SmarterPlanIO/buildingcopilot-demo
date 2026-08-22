# RUNBOOK — Provisioning infra Cabinet Saint Germain / CSG (secrets + IAM + Lambda MCP)

> Date : 20/08/2026. A executer dans **AWS CloudShell** (console, compte 046004768626, region eu-west-1)
> avec un profil admin. Duree ~3 min. Decline du runbook Delacour (clients/delacour/docs/RUNBOOK_PROVISION_DELACOUR.md),
> y compris le fix du gotcha 403 Function URL streaming (lambda:InvokeFunction requis en plus d'InvokeFunctionUrl).
>
> Etat AVANT ce runbook (deja fait depuis le poste local, user Bastien_Kovac / RDSFullAccess, 20/08/2026) :
> - Instance RDS `sp-rag-csg-copros` creee (db.t4g.micro, PG 17.9, 20 Go gp3, public,
>   SG sg-e59223af, chiffree, deletion protection ON, backups 7 j). Mot de passe master
>   temporaire PERDU volontairement -> l'etape 2 ci-dessous le reinitialise vers le secret.
>
> Ce que fait ce script : secrets `palim/csg/*` -> reset mot de passe master RDS ->
> policy + role IAM dedies (isolation : les Lambdas NCG et Delacour ne lisent pas les secrets CSG) ->
> Lambda `palim-csg-mcp` (image **v10**, version de prod : sourcage v1.9 + multi-client fail-closed +
> immatriculations RNIC) + Function URL + slug secret.
>
> APRES ce runbook : prevenir Claude (session locale) qui enchaine : init schema 06a
> (PALIM_CLIENT=csg), creation du user PostgreSQL `mcp_csg_reader` (lecture seule),
> smoke test MCP (initialize + tools/list + isolation tenant), commit config.

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626

# ── 1. Mots de passe + secrets ──────────────────────────────────────────────
PW_ADMIN=$(aws secretsmanager get-random-password --exclude-punctuation --password-length 32 --query RandomPassword --output text)
PW_READER=$(aws secretsmanager get-random-password --exclude-punctuation --password-length 32 --query RandomPassword --output text)
aws secretsmanager create-secret --region $REG --name palim/csg/ragadmin \
  --description "PALIM CSG - ragadmin RDS sp-rag-csg-copros (pipeline, ecriture)" \
  --secret-string "$PW_ADMIN" --query Name --output text
aws secretsmanager create-secret --region $REG --name palim/csg/mcp_reader \
  --description "PALIM CSG - mcp_csg_reader (MCP Lambda, lecture seule)" \
  --secret-string "$PW_READER" --query Name --output text

# ── 2. Reset du mot de passe master RDS vers le secret ──────────────────────
aws rds modify-db-instance --region $REG --db-instance-identifier sp-rag-csg-copros \
  --master-user-password "$PW_ADMIN" --apply-immediately --query "DBInstance.DBInstanceStatus" --output text

# ── 3. IAM : policy + role dedies CSG ───────────────────────────────────────
cat > /tmp/palim-csg-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockEmbedAndRerankModel",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:eu-west-1::foundation-model/amazon.titan-embed-text-v2:0",
        "arn:aws:bedrock:eu-central-1::foundation-model/cohere.rerank-v3-5:0"
      ]
    },
    { "Sid": "BedrockRerank", "Effect": "Allow", "Action": "bedrock:Rerank", "Resource": "*" },
    {
      "Sid": "ReadDbSecret",
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": [
        "arn:aws:secretsmanager:eu-west-1:${ACC}:secret:palim/csg/mcp_reader-*",
        "arn:aws:secretsmanager:eu-west-1:${ACC}:secret:palim/airtable_pat-*"
      ]
    }
  ]
}
EOF
POLICY_ARN=$(aws iam create-policy --policy-name PALIM-MCP-csg-policy \
  --policy-document file:///tmp/palim-csg-policy.json --query Policy.Arn --output text)
cat > /tmp/trust.json <<'EOF'
{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow",
  "Principal": { "Service": "lambda.amazonaws.com" }, "Action": "sts:AssumeRole" } ] }
EOF
aws iam create-role --role-name PALIM-MCP-csg-role \
  --assume-role-policy-document file:///tmp/trust.json --query Role.Arn --output text
aws iam attach-role-policy --role-name PALIM-MCP-csg-role --policy-arn "$POLICY_ARN"
aws iam attach-role-policy --role-name PALIM-MCP-csg-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
echo "Propagation IAM..." && sleep 15

# ── 4. Lambda MCP CSG (image v10 = version de prod) ─────────────────────────
SLUG=$(python3 -c "import secrets; print(secrets.token_hex(24))")
EP=$(aws rds describe-db-instances --region $REG --db-instance-identifier sp-rag-csg-copros \
  --query "DBInstances[0].Endpoint.Address" --output text)
[ "$EP" != "None" ] || { echo "RDS pas encore available, relancer cette section dans 5 min"; exit 1; }
cat > /tmp/env-csg.json <<EOF
{ "Variables": {
  "PALIM_CLIENT": "csg",
  "ASSYNCO_SYNDIC_ALLOWLIST": "CABINET SAINT GERMAIN",
  "ASSYNCO_SYNDIC_NCG": "CABINET SAINT GERMAIN",
  "ENABLE_ASSYNCO": "1",
  "ENABLE_RERANK": "1",
  "DB_HOST": "${EP}",
  "DB_PORT": "5432",
  "DB_NAME": "postgres",
  "DB_USER": "mcp_csg_reader",
  "DB_SECRET_ARN": "palim/csg/mcp_reader",
  "AIRTABLE_PAT_SECRET_ARN": "palim/airtable_pat",
  "AWS_REGION_EMBED": "eu-west-1",
  "AWS_REGION_RERANK": "eu-central-1",
  "AWS_REGION_SECRETS": "eu-west-1",
  "MCP_URL_SLUG": "${SLUG}"
} }
EOF
aws lambda create-function --region $REG --function-name palim-csg-mcp \
  --package-type Image --code ImageUri=${ACC}.dkr.ecr.eu-west-1.amazonaws.com/palim-mcp:v10 \
  --role arn:aws:iam::${ACC}:role/PALIM-MCP-csg-role \
  --memory-size 1024 --timeout 60 --architectures x86_64 \
  --environment file:///tmp/env-csg.json --query FunctionArn --output text
aws lambda wait function-active-v2 --region $REG --function-name palim-csg-mcp

# ── 5. Function URL publique (barriere = slug secret) ───────────────────────
FURL=$(aws lambda create-function-url-config --region $REG --function-name palim-csg-mcp \
  --auth-type NONE --invoke-mode RESPONSE_STREAM --query FunctionUrl --output text)
aws lambda add-permission --region $REG --function-name palim-csg-mcp \
  --statement-id AllowPublicFunctionUrl --action lambda:InvokeFunctionUrl \
  --principal "*" --function-url-auth-type NONE >/dev/null
# InvokeFunctionUrl seul => 403 "Forbidden" au front door en mode streaming
# (gotcha constate le 14/08 sur Delacour) :
aws lambda add-permission --region $REG --function-name palim-csg-mcp \
  --statement-id FnUrlInvokeAction --action lambda:InvokeFunction \
  --principal "*" >/dev/null
rm -f /tmp/palim-csg-policy.json /tmp/env-csg.json /tmp/trust.json

echo ""
echo "==================== PROVISIONING CSG OK ===================="
echo "URL MCP (a garder secrete, slug inclus) : ${FURL}${SLUG}"
echo "============================================================="
```

## Notes

- **Image v10** : version de prod commune aux 3 tenants (NCG, Delacour, CSG) — sourcage v1.9,
  multi-client fail-closed (`ASSYNCO_SYNDIC_ALLOWLIST` vide = aucun acces Assynco),
  resolution copro par immatriculation RNIC canonisee. `ASSYNCO_SYNDIC_NCG` est posee en
  retro-compat, `ASSYNCO_SYNDIC_ALLOWLIST` fait foi.
- **Tenant Assynco** : libelle exact releve dans Airtable le 20/08/2026 = `CABINET SAINT GERMAIN`
  (record Organisation recFrJEVzxTBCXBcA, SIREN 539388876). Ne PAS ajouter d'autres libelles :
  "SOCIETE MARIE SAINT GERMAIN" est un autre syndic (hors tenant).
- **Prealable cote Assynco (Philippe)** : remplir sur la fiche copro Airtable recA6x5yqHWU4JuRV
  ("33 RUE LACÉPÈDE") le champ `Numéro d'immatriculation` = `AB0-835-843` (et idealement
  `Ref client` = `C0216`). Sans cela, la resolution des tools Assynco par code echoue
  (meme gotcha que Delacour avec {Ref client}).
- **Langfuse** : volontairement absent (no-op). Creer un projet Langfuse "PALIM CSG" plus tard
  et ajouter LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST dans l'env de la fonction.
- **La DB est vide** a ce stade : les tools repondront "aucune copro" tant que l'ingestion
  CSG n'a pas tourne (06a + ingest.py --copro AB0835843 avec PALIM_CLIENT=csg).
- Si `create-secret` echoue avec "already exists" (re-run) : utiliser
  `aws secretsmanager put-secret-value --secret-id palim/csg/ragadmin --secret-string "$PW_ADMIN"`.
