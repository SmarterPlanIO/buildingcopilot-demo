# RUNBOOK — Provisioning infra Delacour Patrimoine (secrets + IAM + Lambda MCP)

> ⛔ **EXECUTE LE 14/08/2026 — NE PAS RE-EXECUTER.** Ce script n'est PAS idempotent :
> un re-run genere de NOUVEAUX mots de passe (etape 1, qui echoue en "already exists"
> sans les stocker) puis reset le mot de passe master RDS vers ce mot de passe non
> stocke (etape 2) -> secret et base desynchronises, pipeline et MCP casses.
> Re-run partiel du 20/08 : sans degat (variables shell perdues apres un crash
> CloudShell, les commandes --region ont echoue avant d'agir), mais c'est de la
> chance, pas une protection. Pour toute reprise : executer UNIQUEMENT la section
> concernee, et pour les secrets utiliser `put-secret-value` + refleter le meme
> mot de passe cote RDS dans la MEME session shell.

> Date : 11/08/2026. A executer dans **AWS CloudShell** (console, compte 046004768626, region eu-west-1)
> avec un profil admin. Duree ~3 min.
>
> Etat AVANT ce runbook (deja fait depuis le poste local, user Bastien_Kovac / RDSFullAccess) :
> - Instance RDS `sp-rag-delacour-copros` creee (db.t4g.micro, PG 17.9, 20 Go gp3, public,
>   SG sg-e59223af, chiffree, deletion protection ON). Mot de passe master temporaire PERDU
>   volontairement -> l'etape 3 ci-dessous le reinitialise vers le secret.
>
> Ce que fait ce script : secrets `palim/delacour/*` -> reset mot de passe master RDS ->
> policy + role IAM dedies (isolation : la Lambda NCG ne lit pas les secrets Delacour) ->
> Lambda `palim-delacour-mcp` (image v8 existante) + Function URL + slug secret.
>
> APRES ce runbook : prevenir Claude (session locale) qui enchaine : init schema 06a,
> creation du user PostgreSQL `mcp_delacour_reader` (lecture seule), smoke test MCP, commit config.

## Script (coller tel quel dans CloudShell)

```bash
set -euo pipefail
REG=eu-west-1
ACC=046004768626

# ── 1. Mots de passe + secrets ──────────────────────────────────────────────
PW_ADMIN=$(aws secretsmanager get-random-password --exclude-punctuation --password-length 32 --query RandomPassword --output text)
PW_READER=$(aws secretsmanager get-random-password --exclude-punctuation --password-length 32 --query RandomPassword --output text)
aws secretsmanager create-secret --region $REG --name palim/delacour/ragadmin \
  --description "PALIM Delacour - ragadmin RDS sp-rag-delacour-copros (pipeline, ecriture)" \
  --secret-string "$PW_ADMIN" --query Name --output text
aws secretsmanager create-secret --region $REG --name palim/delacour/mcp_reader \
  --description "PALIM Delacour - mcp_delacour_reader (MCP Lambda, lecture seule)" \
  --secret-string "$PW_READER" --query Name --output text

# ── 2. Reset du mot de passe master RDS vers le secret ──────────────────────
aws rds modify-db-instance --region $REG --db-instance-identifier sp-rag-delacour-copros \
  --master-user-password "$PW_ADMIN" --apply-immediately --query "DBInstance.DBInstanceStatus" --output text

# ── 3. IAM : policy + role dedies Delacour ──────────────────────────────────
cat > /tmp/palim-delacour-policy.json <<EOF
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
        "arn:aws:secretsmanager:eu-west-1:${ACC}:secret:palim/delacour/mcp_reader-*",
        "arn:aws:secretsmanager:eu-west-1:${ACC}:secret:palim/airtable_pat-*"
      ]
    }
  ]
}
EOF
POLICY_ARN=$(aws iam create-policy --policy-name PALIM-MCP-delacour-policy \
  --policy-document file:///tmp/palim-delacour-policy.json --query Policy.Arn --output text)
cat > /tmp/trust.json <<'EOF'
{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow",
  "Principal": { "Service": "lambda.amazonaws.com" }, "Action": "sts:AssumeRole" } ] }
EOF
aws iam create-role --role-name PALIM-MCP-delacour-role \
  --assume-role-policy-document file:///tmp/trust.json --query Role.Arn --output text
aws iam attach-role-policy --role-name PALIM-MCP-delacour-role --policy-arn "$POLICY_ARN"
aws iam attach-role-policy --role-name PALIM-MCP-delacour-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
echo "Propagation IAM..." && sleep 15

# ── 4. Lambda MCP Delacour (image v8 existante) ─────────────────────────────
SLUG=$(python3 -c "import secrets; print(secrets.token_hex(24))")
EP=$(aws rds describe-db-instances --region $REG --db-instance-identifier sp-rag-delacour-copros \
  --query "DBInstances[0].Endpoint.Address" --output text)
[ "$EP" != "None" ] || { echo "RDS pas encore available, relancer cette section dans 5 min"; exit 1; }
cat > /tmp/env-delacour.json <<EOF
{ "Variables": {
  "PALIM_CLIENT": "delacour",
  "ASSYNCO_SYNDIC_ALLOWLIST": "DELACOUR PATRIMOINE,Delacour Patrimoine",
  "ASSYNCO_SYNDIC_NCG": "DELACOUR PATRIMOINE,Delacour Patrimoine",
  "ENABLE_ASSYNCO": "1",
  "ENABLE_RERANK": "1",
  "DB_HOST": "${EP}",
  "DB_PORT": "5432",
  "DB_NAME": "postgres",
  "DB_USER": "mcp_delacour_reader",
  "DB_SECRET_ARN": "palim/delacour/mcp_reader",
  "AIRTABLE_PAT_SECRET_ARN": "palim/airtable_pat",
  "AWS_REGION_EMBED": "eu-west-1",
  "AWS_REGION_RERANK": "eu-central-1",
  "AWS_REGION_SECRETS": "eu-west-1",
  "MCP_URL_SLUG": "${SLUG}"
} }
EOF
aws lambda create-function --region $REG --function-name palim-delacour-mcp \
  --package-type Image --code ImageUri=${ACC}.dkr.ecr.eu-west-1.amazonaws.com/palim-mcp:v8 \
  --role arn:aws:iam::${ACC}:role/PALIM-MCP-delacour-role \
  --memory-size 1024 --timeout 60 --architectures x86_64 \
  --environment file:///tmp/env-delacour.json --query FunctionArn --output text
aws lambda wait function-active-v2 --region $REG --function-name palim-delacour-mcp

# ── 5. Function URL publique (barriere = slug secret) ───────────────────────
FURL=$(aws lambda create-function-url-config --region $REG --function-name palim-delacour-mcp \
  --auth-type NONE --invoke-mode RESPONSE_STREAM --query FunctionUrl --output text)
aws lambda add-permission --region $REG --function-name palim-delacour-mcp \
  --statement-id AllowPublicFunctionUrl --action lambda:InvokeFunctionUrl \
  --principal "*" --function-url-auth-type NONE >/dev/null
# InvokeFunctionUrl seul => 403 "Forbidden" au front door en mode streaming
# (constate le 14/08 ; la policy NCG a la meme declaration en plus) :
aws lambda add-permission --region $REG --function-name palim-delacour-mcp \
  --statement-id FnUrlInvokeAction --action lambda:InvokeFunction \
  --principal "*" >/dev/null
rm -f /tmp/palim-delacour-policy.json /tmp/env-delacour.json /tmp/trust.json

echo ""
echo "==================== PROVISIONING DELACOUR OK ===================="
echo "URL MCP (a garder secrete, slug inclus) : ${FURL}${SLUG}"
echo "=================================================================="
```

## Notes

- **Image v8** : le code v8 lit `ASSYNCO_SYNDIC_NCG` (posee ici avec les libelles Delacour,
  releves dans Airtable le 11/08 : 31 copros sous "DELACOUR PATRIMOINE"). `ASSYNCO_SYNDIC_ALLOWLIST`
  et `PALIM_CLIENT` sont poses en meme temps pour la v9+ (multi-client, fail-closed).
- **Langfuse** : volontairement absent (no-op). Creer un projet Langfuse "PALIM Delacour" plus tard
  et ajouter LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST dans l'env de la fonction.
- **La DB est vide** a ce stade : les tools repondront "aucune copro" tant que l'ingestion
  Delacour n'a pas tourne (06a + pipeline + 06b/08 avec PALIM_CLIENT=delacour).
- Si `create-secret` echoue avec "already exists" (re-run) : utiliser
  `aws secretsmanager put-secret-value --secret-id palim/delacour/ragadmin --secret-string "$PW_ADMIN"`.
