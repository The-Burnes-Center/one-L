# One-L Documentation

Welcome to the comprehensive documentation for the One-L Legal AI Document Review System.

## 📚 **Documentation Index**

### **Getting Started**
- **[Main README](../README.md)** - Project overview, features, and quick setup
- **[Deployment Guide](./DEPLOYMENT.md)** - Complete deployment instructions and troubleshooting
- **[Contributing Guide](../CONTRIBUTING.md)** - Development guidelines and contribution process

### **Technical Documentation**
- **[Architecture Overview](./ARCHITECTURE.md)** - Detailed system architecture and design principles
- **[API Documentation](./API.md)** - REST API and WebSocket API reference

### **Additional Resources**
- **[License](../LICENSE)** - MIT License terms
- **CDK Documentation** - Infrastructure as Code patterns

## 🎯 **Quick Navigation**

### **For Developers**
1. Start with [Architecture Overview](./ARCHITECTURE.md) to understand the system design
2. Follow [Deployment Guide](./DEPLOYMENT.md) for environment setup
3. Reference [API Documentation](./API.md) for integration details
4. See [Contributing Guide](../CONTRIBUTING.md) for development workflows

### **For DevOps/Infrastructure**
1. Review [Deployment Guide](./DEPLOYMENT.md) for infrastructure setup
2. Study [Architecture Overview](./ARCHITECTURE.md) for AWS service integration
3. Check [Main README](../README.md) for technology stack details

### **For Product/Business**
1. Read [Main README](../README.md) for feature overview and business value
2. Review [Architecture Overview](./ARCHITECTURE.md) for scalability and security details

## 🔧 **System Components Quick Reference**

| Component | Technology | Purpose | Documentation |
|-----------|------------|---------|---------------|
| **Frontend** | React 18 + WebSocket | User interface and real-time updates | [Architecture](./ARCHITECTURE.md#frontend-layer) |
| **Authentication** | AWS Cognito | User management and JWT tokens | [Architecture](./ARCHITECTURE.md#authentication--authorization) |
| **API Layer** | API Gateway + WebSocket API | HTTP and real-time communication | [API Docs](./API.md) |
| **AI Engine** | AWS Bedrock (Claude 4 Sonnet) + Step Functions | Legal document analysis workflow orchestration | [Architecture](./ARCHITECTURE.md#aiml-services) |
| **Workflow Orchestration** | AWS Step Functions | Multi-stage document processing pipeline | [Architecture](./ARCHITECTURE.md#step-functions-workflow-document-review) |
| **Storage** | S3 + DynamoDB + OpenSearch | Document and data storage | [Architecture](./ARCHITECTURE.md#storage-layer) |
| **Infrastructure** | AWS CDK (Python) | Infrastructure as Code | [Deployment](./DEPLOYMENT.md) |

## 🚀 **Quick Start Commands**

```bash
# Clone and setup
git clone <repository-url> && cd one-L
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Deploy infrastructure
cdk bootstrap && cdk deploy

# Local development
cd one_l/user_interface && npm install && npm start
```

## 🔍 **Common Use Cases**

### **Document Analysis Workflow (Step Functions)**
1. **Upload Documents**: Reference docs (MA requirements) + vendor submissions
2. **Trigger Workflow**: POST to `/agent/review` starts Step Functions state machine
3. **Step Functions Orchestration**: Multi-stage workflow (Initialize → Split → Analyze → Merge → Redline → Save)
4. **AI Analysis**: Automatic conflict detection using Claude 4 Sonnet with RAG context
5. **Review Results**: Download redlined documents with conflict annotations
6. **Session Management**: Organize work into tracked sessions with complete audit trail

### **System Administration**
1. **Knowledge Base Management**: Upload and sync reference documents
2. **User Management**: Cognito-based authentication and authorization
3. **Monitoring**: CloudWatch logs and metrics for system health

### **API Integration**
1. **File Upload**: Presigned URLs for secure client-side uploads
2. **Document Processing**: RESTful API (`/agent/review`) triggers Step Functions workflow
3. **Workflow Status**: Poll `/agent/job-status` or subscribe via WebSocket for real-time progress
4. **Real-time Updates**: WebSocket for Step Functions execution progress and completion notifications

## 🛡️ **Security & Compliance**

- **Zero Trust Architecture**: Authentication required at every layer
- **End-to-End Encryption**: TLS in transit, encryption at rest
- **Fine-grained IAM**: Least privilege access patterns
- **Audit Trail**: Complete session and analysis tracking
- **Data Privacy**: Configurable retention and deletion policies

## 📊 **Performance Characteristics**

- **Scalability**: Serverless auto-scaling up to 10,000 concurrent users
- **Response Time**: < 2 seconds for API calls, 2-5 minutes for AI analysis
- **Availability**: 99.9% uptime with multi-AZ deployment
- **Cost**: Pay-per-use serverless model with predictable scaling

## 📞 **Support and Resources**

### **Technical Support**
- **Issues**: Open GitHub issues for bugs or feature requests
- **Discussions**: Use GitHub discussions for questions and ideas
- **Documentation**: Comprehensive guides in this docs/ directory

### **AWS Resources**
- **CDK Documentation**: [AWS CDK Guide](https://docs.aws.amazon.com/cdk/)
- **Bedrock Documentation**: [AWS Bedrock User Guide](https://docs.aws.amazon.com/bedrock/)
- **Lambda Best Practices**: [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/)

### **Community**
- **Contributing**: Professional, respectful collaboration expected
- **License**: MIT License for open collaboration

---

For questions or contributions, please open an issue in the repository.
