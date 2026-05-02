data "aws_partition" "current" {}

# ─────────────────────────────────────────────
# OIDC Identity Provider
# ─────────────────────────────────────────────

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["1c58a3a8518e8759bf075b76b750d4f2df264fcd"]
}

# ─────────────────────────────────────────────
# IAM Role for GitHub Actions
# ─────────────────────────────────────────────

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_org}/${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_org}/${var.github_repo}:pull_request",
      ]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.project_name}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json
}

# ─────────────────────────────────────────────
# IAM Policy — Terraform + Deploy permissions
# ─────────────────────────────────────────────

data "aws_iam_policy_document" "terraform_permissions" {
  # S3 state backend
  statement {
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket}",
      "arn:${data.aws_partition.current.partition}:s3:::${var.state_bucket}/*",
    ]
  }

  # DynamoDB state locking
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.lock_table}",
    ]
  }

  # ECR — auth token (requires *)
  statement {
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # ECR — repository operations
  statement {
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:PutImage",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeRepositories",
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:DeleteLifecyclePolicy",
      "ecr:SetRepositoryPolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:DeleteRepositoryPolicy",
      "ecr:TagResource",
      "ecr:UntagResource",
      "ecr:ListTagsForResource",
      "ecr:DescribeImages",
      "ecr:PutImageScanningConfiguration",
      "ecr:PutImageTagMutability",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${var.aws_account_id}:repository/${var.project_name}-*",
    ]
  }

  # Lambda
  statement {
    actions = ["lambda:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${var.aws_account_id}:function:${var.project_name}-*",
      "arn:${data.aws_partition.current.partition}:lambda:${var.aws_region}:${var.aws_account_id}:event-source-mapping:*",
    ]
  }

  statement {
    actions   = ["lambda:ListEventSourceMappings"]
    resources = ["*"]
  }

  # SQS
  statement {
    actions = ["sqs:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:sqs:${var.aws_region}:${var.aws_account_id}:${var.project_name}-*",
    ]
  }

  # SNS
  statement {
    actions = ["sns:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:sns:${var.aws_region}:${var.aws_account_id}:${var.project_name}-*",
    ]
  }

  # CloudWatch Logs (DescribeLogGroups requires * resource)
  statement {
    actions = ["logs:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.project_name}-*",
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.project_name}-*:*",
    ]
  }

  statement {
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }

  # CloudWatch Alarms + Dashboards
  statement {
    actions = ["cloudwatch:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:cloudwatch:${var.aws_region}:${var.aws_account_id}:alarm:${var.project_name}-*",
      "arn:${data.aws_partition.current.partition}:cloudwatch::${var.aws_account_id}:dashboard/${var.project_name}-*",
    ]
  }

  # EventBridge
  statement {
    actions = ["events:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:events:${var.aws_region}:${var.aws_account_id}:rule/${var.project_name}-*",
    ]
  }

  # IAM — manage Lambda roles
  statement {
    actions = [
      "iam:GetRole",
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:PassRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:GetRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:role/${var.project_name}-*",
    ]
  }

  # VPC / Networking (ec2:Describe* requires * resource)
  statement {
    actions = [
      "ec2:Describe*",
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:CreateInternetGateway",
      "ec2:DeleteInternetGateway",
      "ec2:AttachInternetGateway",
      "ec2:DetachInternetGateway",
      "ec2:CreateNatGateway",
      "ec2:DeleteNatGateway",
      "ec2:AllocateAddress",
      "ec2:ReleaseAddress",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:CreateVpcEndpoint",
      "ec2:DeleteVpcEndpoints",
      "ec2:ModifyVpcEndpoint",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = ["*"]
  }

  # RDS (DescribeDBEngineVersions requires * resource)
  statement {
    actions = ["rds:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${var.aws_account_id}:db:${var.project_name}-*",
      "arn:${data.aws_partition.current.partition}:rds:${var.aws_region}:${var.aws_account_id}:subgrp:${var.project_name}-*",
    ]
  }

  statement {
    actions   = ["rds:DescribeDBEngineVersions"]
    resources = ["*"]
  }

  # Secrets Manager
  statement {
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
      "secretsmanager:GetResourcePolicy",
      "secretsmanager:ListSecretVersionIds",
      "secretsmanager:CreateSecret",
      "secretsmanager:UpdateSecret",
      "secretsmanager:DeleteSecret",
      "secretsmanager:TagResource",
      "secretsmanager:UntagResource",
      "secretsmanager:PutSecretValue",
      "secretsmanager:RotateSecret",
      "secretsmanager:PutResourcePolicy",
      "secretsmanager:DeleteResourcePolicy",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.project_name}/*",
      "arn:${data.aws_partition.current.partition}:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:jobspy/*",
    ]
  }

  # OIDC provider self-management
  statement {
    actions = [
      "iam:GetOpenIDConnectProvider",
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${var.aws_account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }

  # Serverless Application Repository (for RDS rotation Lambda)
  statement {
    actions = [
      "serverlessrepo:GetApplication",
      "serverlessrepo:CreateCloudFormationTemplate",
      "serverlessrepo:GetCloudFormationTemplate",
    ]
    resources = ["*"]
  }

  # CloudFormation (for RDS rotation Lambda stack)
  statement {
    actions = ["cloudformation:*"]
    resources = [
      "arn:${data.aws_partition.current.partition}:cloudformation:${var.aws_region}:${var.aws_account_id}:stack/serverlessrepo-${var.project_name}-*",
    ]
  }
}

resource "aws_iam_role_policy" "terraform" {
  name   = "${var.project_name}-github-actions-terraform"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.terraform_permissions.json
}
