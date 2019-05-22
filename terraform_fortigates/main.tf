provider "aws" {
  access_key                     = "${var.access_key}"
  secret_key                     = "${var.secret_key}"
  region                         = "${var.aws_region}"
}

data "aws_ami" "fortigate" {
  most_recent = true

  filter {
    name                         = "name"
    values                       = ["FortiGate-VM64-AWSONDEMAND build0231 (6.0.4) GA*"]
  }

  filter {
    name                         = "virtualization-type"
    values                       = ["hvm"]
  }

  owners                         = ["679593333241"] # Canonical
}

module "ec2-sg" {
  source                         = "../modules/security_group"
  access_key                     = "${var.access_key}"
  secret_key                     = "${var.secret_key}"
  aws_region                     = "${var.aws_region}"
  vpc_id                         = "${var.vpc_id}"
  name                           = "${var.sg_name}"
  ingress_to_port                = 0
  ingress_from_port              = 0
  ingress_protocol               = "-1"
  ingress_cidr_for_access        = "0.0.0.0/0"
  egress_to_port                 = 0
  egress_from_port               = 0
  egress_protocol                = "-1"
  egress_cidr_for_access         = "0.0.0.0/0"
  customer_prefix                = "${var.customer_prefix}"
  environment                    = "${var.environment}"
}

module "fgt-sns" {
  source = "../modules/sns"
  access_key                     = "${var.access_key}"
  secret_key                     = "${var.secret_key}"
  aws_region                     = "${var.aws_region}"
  sns_topic                      = "${var.sns_topic}"
  environment                    = "${var.environment}"
  customer_prefix                = "${var.customer_prefix}"
  notification_url               = "${var.api_gateway_url}"

}

module "nlb" {
  source                         = "../modules/nlb"
  access_key                     = "${var.access_key}"
  secret_key                     = "${var.secret_key}"
  aws_region                     = "${var.aws_region}"
  vpc_id                         = "${var.vpc_id}"
  subnet1_id                     = "${var.public1_subnet_id}"
  subnet2_id                     = "${var.public2_subnet_id}"
  customer_prefix                = "${var.customer_prefix}"
  environment                    = "${var.environment}"

}

module "ec2-asg-byol" {
  source = "../modules/autoscale"
  access_key                     = "${var.access_key}"
  secret_key                     = "${var.secret_key}"
  aws_region                     = "${var.aws_region}"
  vpc_id                         = "${var.vpc_id}"
  instance_type                  = "${var.instance_type}"
  ami_id                         = "${data.aws_ami.fortigate.id}"
  public_subnet1_id              = "${var.public1_subnet_id}"
  public_subnet2_id              = "${var.public2_subnet_id}"
  private_subnet1_id             = "${var.private1_subnet_id}"
  private_subnet2_id             = "${var.private2_subnet_id}"
  security_group                 = "${module.ec2-sg.id}"
  key_name                       = "${var.keypair}"
  max_size                       = "${var.max_size-byol}"
  min_size                       = "${var.min_size-byol}"
  desired                        = "${var.desired-byol}"
  userdata                       = "${path.cwd}/fortigate-userdata.tpl"
  topic_arn                      = "${module.fgt-sns.arn}"
  target_group_arns              = "${module.nlb.target_group_arns}"
  customer_prefix                = "${var.customer_prefix}"
  environment                    = "${var.environment}"
  license                        = "byol"
  s3_license_bucket              = "${var.s3_license_bucket}"
}
module "ec2-asg-paygo" {
  source = "../modules/autoscale"
  access_key                     = "${var.access_key}"
  secret_key                     = "${var.secret_key}"
  aws_region                     = "${var.aws_region}"
  vpc_id                         = "${var.vpc_id}"
  instance_type                  = "${var.instance_type}"
  ami_id                         = "${data.aws_ami.fortigate.id}"
  public_subnet1_id              = "${var.public1_subnet_id}"
  public_subnet2_id              = "${var.public2_subnet_id}"
  private_subnet1_id             = "${var.private1_subnet_id}"
  private_subnet2_id             = "${var.private2_subnet_id}"
  security_group                 = "${module.ec2-sg.id}"
  key_name                       = "${var.keypair}"
  max_size                       = "${var.max_size-paygo}"
  min_size                       = "${var.min_size-paygo}"
  desired                        = "${var.desired-paygo}"
  userdata                       = "${path.cwd}/fortigate-userdata.tpl"
  topic_arn                      = "${module.fgt-sns.arn}"
  target_group_arns              = "${module.nlb.target_group_arns}"
  customer_prefix                = "${var.customer_prefix}"
  environment                    = "${var.environment}"
  license                        = "paygo"
  s3_license_bucket              = "${var.s3_license_bucket}"
}


