provider "aws" {
  region = "us-east-1"
}

# --- VPC Config ---
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = { Name = "auditor-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
}

data "aws_availability_zones" "available" {}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index}/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "auditor-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "auditor-private-${count.index}" }
}

# --- Route Tables ---
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- Database Subnet Group & RDS Instance ---
resource "aws_db_subnet_group" "rds" {
  name       = "auditor-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_security_group" "rds_sg" {
  name        = "auditor-rds-sg"
  vpc_id      = aws_vpc.main.id
  description = "Access to RDS from Fargate tasks only"

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.fargate_tasks.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "postgres" {
  identifier             = "auditor-postgres"
  engine                 = "postgres"
  engine_version         = "15.4"
  instance_class         = "db.t4g.micro"
  allocated_storage      = 20
  db_name                = "audit_db"
  username               = "db_admin"
  password               = "ChooseAStrongPassword123"
  db_subnet_group_name   = aws_db_subnet_group.rds.name
  vpc_security_group_ids = [aws_security_group.rds_sg.id]
  skip_final_snapshot    = true
}

# --- Load Balancer Config ---
resource "aws_security_group" "alb" {
  name   = "auditor-alb-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "main" {
  name               = "auditor-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "web" {
  name        = "auditor-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# --- ECS Fargate Security Group ---
resource "aws_security_group" "fargate_tasks" {
  name   = "auditor-fargate-tasks-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- IAM Roles for ECS ---
resource "aws_iam_role" "ecs_execution" {
  name = "auditor-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --- ECS Cluster & Service ---
resource "aws_ecs_cluster" "main" {
  name = "auditor-cluster"
}

resource "aws_ecs_task_definition" "web" {
  family                   = "auditor-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name      = "web"
    image     = "CHANGE_ME_TO_YOUR_ECR_IMAGE_URL"
    essential = true
    portMappings = [{
      containerPort = 8000
      hostPort      = 8000
    }]
    environment = [
      { name = "DATABASE_URL", value = "postgresql://db_admin:ChooseAStrongPassword123@${aws_db_instance.postgres.endpoint}/audit_db" },
      { name = "GEMINI_API_KEY", value = "CHANGE_ME_TO_YOUR_GEMINI_API_KEY" },
      { name = "OPENAI_API_KEY", value = "CHANGE_ME_TO_YOUR_OPENAI_API_KEY" },
      { name = "API_HOST", value = "0.0.0.0" },
      { name = "API_PORT", value = "8000" },
      { name = "API_KEY", value = "super-secret-audit-key-99" }
    ]
  }])
}

resource "aws_ecs_service" "web" {
  name            = "auditor-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.fargate_tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}
