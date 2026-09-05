# Deploy identity for the owner's Claude Code sessions and CI: an IAM user whose
# access key lives in the tooling's environment settings, so routine deploys stop
# running as the account root through browser logins that expire every few hours.
#
# Scope: everything this stack manages, no more. IAM writes are limited to this
# stack's role, policy and user names; secrets to this stack's prefix. The access
# key is created by hand in the console (never in state or chat) and rotated there.
resource "aws_iam_user" "deployer" {
  name = "${local.name}-deployer"
  tags = { Purpose = "Lightwork prod deploys (Terraform, CodeBuild, ECS)" }
}

data "aws_iam_policy_document" "deployer" {
  # Read-only discovery Terraform and the CLI need everywhere.
  statement {
    sid = "Describe"
    actions = [
      "ec2:Describe*", "elasticloadbalancing:Describe*", "rds:Describe*", "rds:ListTagsForResource",
      "elasticfilesystem:Describe*", "ecs:Describe*", "ecs:List*", "ecr:Describe*", "ecr:List*",
      "ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
      "logs:Describe*", "logs:Get*", "logs:FilterLogEvents", "codebuild:BatchGet*", "codebuild:List*",
      "acm:Describe*", "acm:List*", "iam:Get*", "iam:List*", "sts:GetCallerIdentity",
      "secretsmanager:ListSecrets", "s3:ListAllMyBuckets", "ses:Get*", "ses:List*",
    ]
    resources = ["*"]
  }

  # Terraform state.
  statement {
    sid     = "State"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      "arn:aws:s3:::lightwork-tfstate-${data.aws_caller_identity.current.account_id}",
      "arn:aws:s3:::lightwork-tfstate-${data.aws_caller_identity.current.account_id}/*",
    ]
  }

  # Build source upload and the image build.
  statement {
    sid       = "Build"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.build.arn, "${aws_s3_bucket.build.arn}/*"]
  }
  statement {
    sid       = "CodeBuild"
    actions   = ["codebuild:*"]
    resources = [aws_codebuild_project.image.arn]
  }
  statement {
    sid       = "Ecr"
    actions   = ["ecr:*"]
    resources = [aws_ecr_repository.app.arn]
  }

  # Runtime: cluster, service, task definitions, one-off tasks, logs.
  statement {
    sid       = "Ecs"
    actions   = ["ecs:*"]
    resources = ["*"]
  }
  statement {
    sid     = "Logs"
    actions = ["logs:*"]
    resources = [
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/ecs/${local.name}*",
      "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/codebuild/${local.name}*",
    ]
  }

  # Network, load balancer, database, state volume, certificates, email.
  statement {
    sid = "Infra"
    actions = [
      "ec2:*Vpc*", "ec2:*Subnet*", "ec2:*RouteTable*", "ec2:*Route", "ec2:*InternetGateway*",
      "ec2:*SecurityGroup*", "ec2:*NetworkInterface*", "ec2:*Address*", "ec2:CreateTags", "ec2:DeleteTags",
      "ec2:ModifyVpcAttribute", "elasticloadbalancing:*", "rds:*", "elasticfilesystem:*", "acm:*", "ses:*",
    ]
    resources = ["*"]
  }

  # This stack's secrets only.
  statement {
    sid       = "Secrets"
    actions   = ["secretsmanager:*"]
    resources = ["arn:aws:secretsmanager:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:secret:${local.name}/*"]
  }

  # IAM: only this stack's roles, policies and users, plus handing roles to services.
  statement {
    sid = "IamStack"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy", "iam:TagRole", "iam:UntagRole",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PassRole",
      "iam:CreatePolicy", "iam:DeletePolicy", "iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:TagPolicy",
      "iam:CreateUser", "iam:DeleteUser", "iam:TagUser", "iam:AttachUserPolicy", "iam:DetachUserPolicy",
    ]
    resources = [
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${local.name}-*",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${local.name}-*",
      "arn:aws:iam::${data.aws_caller_identity.current.account_id}:user/${local.name}-*",
    ]
  }
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "deployer" {
  name   = "${local.name}-deployer"
  policy = data.aws_iam_policy_document.deployer.json
}

resource "aws_iam_user_policy_attachment" "deployer" {
  user       = aws_iam_user.deployer.name
  policy_arn = aws_iam_policy.deployer.arn
}
