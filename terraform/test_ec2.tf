data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023*-arm64"]
  }

  filter {
    name   = "architecture"
    values = ["arm64"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_iam_policy_document" "test_ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "test_ec2" {
  name               = "aws-job-scraper-test-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.test_ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "test_ec2_ssm" {
  role       = aws_iam_role.test_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "test_ec2_secrets" {
  role       = aws_iam_role.test_ec2.name
  policy_arn = "arn:aws:iam::aws:policy/SecretsManagerReadWrite"
}

resource "aws_iam_instance_profile" "test_ec2" {
  name = "aws-job-scraper-test-ec2-profile"
  role = aws_iam_role.test_ec2.name
}

resource "aws_instance" "test_ec2" {
  ami                         = data.aws_ami.al2023_arm64.id
  instance_type               = "t4g.nano"
  subnet_id                   = module.networking.private_subnet_ids[0]
  vpc_security_group_ids      = [module.networking.test_ec2_security_group_id]
  iam_instance_profile        = aws_iam_instance_profile.test_ec2.name
  associate_public_ip_address = false

  tags = {
    Name = "aws-job-scraper-test-ec2"
  }
}
