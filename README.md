# 📌 One-L Legal AI Document Review System

> _AI-powered legal document analysis platform that automatically identifies conflicts between vendor contract submissions and Massachusetts state requirements._

---

## 🎬 Demo

The system provides a React-based interface with session management, real-time progress tracking, and automated redlined document generation.

> **Key Demo Flow**: Upload vendor contracts → Step Functions workflow orchestrates AI analysis against MA legal requirements → Download redlined documents with conflict annotations

---

## 🧠 What It Does

- 🔍 **Automated Conflict Detection**: AI-powered analysis using Claude 4 Sonnet to identify conflicts between vendor contract language and Massachusetts legal requirements

- 📝 **Intelligent Document Redlining**: Automatically generates marked-up documents highlighting specific conflicts, modifications, and legal issues  
  Creates downloadable redlined versions with detailed conflict annotations and rationale.

- 🏛️ **Massachusetts Legal Compliance**: Specialized knowledge base containing MA state requirements, ITS Terms & Conditions, EOTSS policies, and procurement regulations  
  Uses RAG (Retrieval-Augmented Generation) for accurate legal context and analysis.

- 📊 **Session-Based Workflow**: Organize document reviews into tracked sessions with complete audit trails and historical results  
  Real-time progress tracking via WebSocket integration for Step Functions workflow execution (2-5 minute AI analysis tasks).

---

## 🧱 Architecture

![System Architecture](./docs/architecture-diagram.png)

> **Note**: The architecture diagram shows the complete system flow. The document analysis process is orchestrated by **AWS Step Functions**, which coordinates all Lambda functions in a multi-stage workflow.

The system follows a modern serverless microservices architecture on AWS, orchestrated by **AWS Step Functions**:

```
[React SPA] → [CloudFront CDN] → [API Gateway + WebSocket API] → 
[Step Functions State Machine] → [Lambda Functions] → 
[Bedrock AI + Knowledge Base] → [OpenSearch + S3 + DynamoDB]
```

**Key Components:**
- **Frontend**: React SPA with real-time WebSocket integration for Step Functions progress tracking
- **Authentication**: AWS Cognito with OAuth 2.0 flows
- **Workflow Orchestration**: **AWS Step Functions** - Orchestrates the entire document analysis workflow (11 stages)
- **AI Engine**: AWS Bedrock with Claude 4 Sonnet and Knowledge Base RAG (invoked by Step Functions)
- **Compute**: AWS Lambda functions (12 functions) orchestrated by Step Functions state machine
- **Storage**: Multi-tier S3 architecture with OpenSearch Serverless vector database
- **Infrastructure**: AWS CDK with modular construct-based deployment

---

## 🧰 Tech Stack

| Layer              | Tools & Frameworks                                           |
|--------------------|--------------------------------------------------------------|
| **Frontend**       | React 18, React Router, Custom WebSocket Service            |
| **Workflow Orchestration** | **AWS Step Functions** - Multi-stage state machine for document processing |
| **Compute**        | AWS Lambda (Python 3.12) - 12 functions orchestrated by Step Functions |
| **API Layer**      | API Gateway (REST), WebSocket API for real-time updates     |
| **AI/ML**          | AWS Bedrock (Claude 4 Sonnet), Knowledge Base, Titan Embeddings |
| **Storage**        | S3 (3-tier), DynamoDB, OpenSearch Serverless               |
| **Auth**           | AWS Cognito User Pool, JWT Tokens, OAuth 2.0               |
| **Infra/DevOps**   | AWS CDK (Python), CloudFront, IAM, CloudWatch              |
| **Real-time**      | WebSocket API for Step Functions progress tracking          |

---

## 🧪 Setup

### Prerequisites
- AWS CLI configured with appropriate permissions
- Node.js 18+ for frontend development
- Python 3.12+ for CDK deployment
- AWS CDK v2 installed (`npm install -g aws-cdk`)

### Deployment

```bash
# Clone the repository
git clone https://github.com/The-Burnes-Center/one-L.git
cd one-L

# Set up Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate.bat

# Install CDK dependencies
pip install -r requirements.txt

# Configure constants (REQUIRED before deployment)
# Edit constants.py to set your stack name and Cognito domain
# Dev branch: STACK_NAME = "OneL-DV2"
# Prod branch: STACK_NAME = "OneL-Prod"

# Deploy the infrastructure (creates Step Functions state machine + all Lambda functions)
cdk bootstrap  # One-time setup per AWS account/region
cdk deploy

# Build and deploy frontend (handled automatically by CDK)
cd one_l/user_interface
npm install
npm run build
```

**What Gets Deployed:**
- **Step Functions State Machine**: Orchestrates the 11-stage document analysis workflow
- **12 Lambda Functions**: Each handles a specific stage in the Step Functions workflow
- **API Gateway**: REST API and WebSocket API endpoints
- **Storage**: S3 buckets, DynamoDB tables, OpenSearch Serverless collection
- **Knowledge Base**: AWS Bedrock Knowledge Base with vector embeddings
- **Frontend**: React app hosted on S3 with CloudFront CDN

### Environment Configuration
The system automatically generates runtime configuration post-deployment. No manual `.env` setup required.

---

## 🧠 Core Modules

| Module                          | Description                                                               |
|---------------------------------|---------------------------------------------------------------------------|
| **`one_l/one_l_stack.py`**           | Main CDK stack orchestrating all AWS resources with dependency management |
| **`one_l/agent_api/functions/stepfunctions/stepfunctions.py`** | **Step Functions construct** - Creates state machine and all 12 Lambda functions for workflow orchestration |
| **`one_l/agent_api/functions/stepfunctions/*/lambda_function.py`** | **12 Lambda functions** - Each handles a specific stage (Initialize, Split, Analyze, Retrieve, Identify, Merge, Redline, Save, Cleanup, HandleError, StartWorkflow, JobStatus) |
| **`one_l/agent_api/agent/model.py`** | Claude 4 Sonnet integration with sophisticated legal prompting        |
| **`one_l/agent_api/agent/tools.py`** | Document redlining and DynamoDB operations for analysis results         |
| **`one_l/agent_api/agent/prompts/`** | AI prompts and output models for conflict detection and analysis         |
| **`one_l/agent_api/functions/knowledge_management/`** | S3 operations, Knowledge Base sync, and session management      |
| **`one_l/agent_api/functions/websocket/`**     | Real-time communication handlers for Step Functions progress tracking                   |
| **`one_l/user_interface/src/`**      | React frontend with session management and real-time Step Functions updates            |

---

## 🌍 AI Analysis Flow (Step Functions Workflow)

The system employs a sophisticated multi-stage AI analysis workflow orchestrated by AWS Step Functions:

1. 📄 **Document Ingestion** → Upload vendor submissions and reference documents with session-based organization  
2. 🔍 **Knowledge Base Sync** → Vector embedding and indexing using Titan Embed Text v2 for reference documents  
3. 🚀 **Workflow Initiation** → StartWorkflow Lambda triggers Step Functions state machine  
4. 📑 **Document Splitting** → Chunks large documents for parallel processing  
5. 🧠 **Structure Analysis** → Analyzes document structure and generates adaptive KB queries  
6. 🔎 **Knowledge Retrieval** → Retrieves relevant context from knowledge base for all queries  
7. ⚖️ **Conflict Detection** → Claude 4 Sonnet identifies contradictions, modifications, omissions, and reversals  
8. 🔀 **Result Merging** → Combines parallel chunk results into unified analysis  
9. 📝 **Document Redlining** → Generates marked-up documents with detailed conflict annotations  
10. 💾 **Results Storage** → Structured conflict data saved to DynamoDB with session tracking  
11. 🧹 **Cleanup** → Removes temporary processing files

---

## 🛡️ Security & Privacy

- **Zero Trust Architecture**: Every service interaction requires authentication and authorization
- **End-to-End Encryption**: Data encrypted in transit (TLS) and at rest (S3/DynamoDB encryption)
- **Fine-Grained IAM**: Least privilege access with service-specific roles and policies
- **User Authentication**: AWS Cognito with strong password policies and JWT token management
- **Audit Trail**: Complete session tracking and analysis history for compliance
- **No Data Retention**: Documents processed securely with configurable retention policies

---

## 📖 Documentation

### Documentation Guides
All implementation details are documented in the local documentation files below. For additional context or historical reference, see the external implementation guide (if available).

| Guide | Description |
|-------|-------------|
| **[Deployment Guide](./docs/DEPLOYMENT.md)** | Step-by-step infrastructure deployment |
| **[Architecture Overview](./docs/ARCHITECTURE.md)** | Technical system architecture details |
| **[API Documentation](./docs/API.md)** | REST and WebSocket API reference |

### Development Commands

| Command | Description |
|---------|-------------|
| `cdk ls` | List all stacks in the app |
| `cdk synth` | Synthesize CloudFormation template |
| `cdk deploy` | Deploy infrastructure to AWS |
| `cdk diff` | Compare deployed stack with current state |
| `cdk destroy` | Remove all AWS resources |
| `npm run start` | Run React frontend locally (from user_interface/) |

---

## 🤝 Contributing

Pull requests are welcome. For major changes, please open an issue first.

---

## 📄 License

MIT License – see `LICENSE` for details.

---

## 👥 Authors & Acknowledgements

- Built by [Ritik Bompilwar](https://www.linkedin.com/in/ritik-bompilwar), Divya Hegde, Ashley La Rotonda, and Neha Tummala
- Developed for AI4Impact initiative  
- Powered by AWS serverless technologies

