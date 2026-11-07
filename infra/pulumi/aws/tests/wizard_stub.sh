#!/usr/bin/env bash
# A stand-in for every tool the wizards call: it logs the call and answers from fakes.
name=$(basename "$0")
ACCOUNT="2109876${STUB_ACCOUNT_TAIL}"  # a fake 12-digit account, never written whole
OTHER="${ACCOUNT:6}${ACCOUNT:0:6}"  # another account the wizard was never told about
printf '%s %s\n' "$name" "$*" >> "$STUB_LOG"
last="${!#}"
case "$name $*" in
  "pulumi version") echo v3.272.0 ;;
  "pulumi stack output bucket") if [[ "$PWD" == *reference-workload ]]; then echo owner-reference-bucket-5e6f; else echo owner-secret-bucket-1a2b; fi ;;
  "pulumi stack output apiUrl") echo https://abcdefghij.lambda-url.us-east-1.on.aws/ ;;
  "pulumi stack output collectorFunction") echo data-finops-dev-collector ;;
  "pulumi stack output apiFunction") echo data-finops-dev-api ;;
  "pulumi stack output producerPolicyArn") echo arn:aws:iam::${ACCOUNT}:policy/data-finops-producer-1 ;;
  "pulumi stack output readerPolicyArn") echo arn:aws:iam::${ACCOUNT}:policy/data-finops-reader-1 ;;
  "pulumi stack output database") echo reference_workload ;;
  "pulumi stack output workgroup") echo data-finops-reference ;;
  "pulumi stack output") if [[ "$PWD" == *reference-workload ]]; then echo "bucket owner-reference-bucket-5e6f"
    else echo "bucket owner-secret-bucket-1a2b"; echo "apiUrl https://abcdefghij.lambda-url.us-east-1.on.aws/"; fi ;;
  "pulumi stack export"*) echo '{}' > "$last" ;;
  "pulumi config get"*) exit 1 ;;
  "pulumi preview"*|"pulumi up"*|"pulumi destroy"*)  # Pulumi prints the outputs unless told not to
    echo "pulumi did $1 on arn:aws:s3:::data-finops-1a2b3c4"  # an auto-named bucket, before any output
    [[ "$*" == *--suppress-outputs* ]] || echo "bucket: owner-secret-bucket-1a2b" ;;
  "pulumi "*) echo "pulumi did $1 in ${ACCOUNT}" ;;
  "aws sts get-caller-identity"*"--query Account"*) echo ${ACCOUNT} ;;
  "aws sts get-caller-identity"*) echo arn:aws:iam::${ACCOUNT}:user/owner-deployer-profile ;;
  "aws iam list-entities-for-policy"*) printf '0\t0\t0\n' ;;
  "aws lambda invoke"*) printf '{"exit_code":0,"bucket":"owner-secret-bucket-1a2b","account":"%s","owner":"%s"}\n' "$ACCOUNT" "$OTHER" > "$last"; echo '{"StatusCode": 200}' ;;
  "aws s3api head-bucket --bucket owner-secret-bucket-1a2b"*) [[ "${STUB_KEPT:-}" == 1 ]] && exit 0
    echo "An error occurred (404) when calling the HeadBucket operation: Not Found" >&2; exit 254 ;;
  # After a destroy, each read fails as the real service does for a missing resource.
  "aws glue get-database"*) if [[ "${STUB_DENIED:-}" == 1 ]]; then
      echo "An error occurred (AccessDeniedException) when calling the GetDatabase operation" >&2
    else echo "An error occurred (EntityNotFoundException) when calling the GetDatabase operation" >&2; fi
    exit 254 ;;
  "aws lambda get-function"*) echo "An error occurred (ResourceNotFoundException) when calling the GetFunction operation" >&2; exit 254 ;;
  "aws iam get-policy"*) echo "An error occurred (NoSuchEntity) when calling the GetPolicy operation" >&2; exit 254 ;;
  "aws s3api head-bucket"*) echo "An error occurred (404) when calling the HeadBucket operation: Not Found" >&2; exit 254 ;;
  "aws athena get-work-group"*) echo "An error occurred (InvalidRequestException) when calling the GetWorkGroup operation: WorkGroup is not found." >&2; exit 254 ;;
  "aws budgets describe-budget"*"--budget-name data-finops-reference-budget "*) echo "An error occurred (NotFoundException) when calling the DescribeBudget operation" >&2; exit 254 ;;
  "aws budgets describe-budget"*) echo "An error occurred (ValidationException) when calling the DescribeBudget operation" >&2; exit 254 ;;
  "aws "*) echo "aws ok for owner-export-bucket-9c1d" ;;
  "uv run --frozen python -m deployment.state_check ids"*) echo "${ACCOUNT}:data-finops-reference-budget" ;;
  "uv run --frozen python -m deployment.state_check policies"*) mkdir -p "$last"; echo '{}' > "$last/data-finops-collector.json"; echo data-finops-collector.json ;;
  "uv run --frozen python -m deployment.state_check"*) echo ok ;;
  "uv "*) echo "uv ok" ;;
  "awscurl "*) echo '{"status":"ok","from":"https://abcdefghij.lambda-url.us-east-1.on.aws/"}' ;;
esac
