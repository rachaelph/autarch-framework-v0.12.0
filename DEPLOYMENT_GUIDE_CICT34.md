# CICT-34 Deployment Guide: Foundry Invoice Task Determination Agent

**Status:** Ready for Deployment  
**Target:** Foundry Project at https://cict-ai-cus-foundry.openai.azure.com  
**Demo Date:** October 22, 2026

## Quick Start: Deploy the Agent

### Option A: Deploy to Foundry Platform (Recommended for Demo)

1. **Register Agent in Foundry Portal**
   - Navigate to Foundry project: https://cict-ai-cus-foundry.openai.azure.com
   - Create new agent with:
     - **Name:** invoice-task-determination-v1
     - **Display Name:** Invoice Task Determination Agent
     - **Model:** gpt-5.1
     - **Description:** Analyzes invoice packages and determines CapEx/OpEx categorization

2. **Copy System Prompt**
   ```
   [Copy full system prompt from autarch/capex/foundry_agent_definition.py]
   ```

3. **Configure Agent Settings**
   - Temperature: 0.7
   - Max Tokens: 2000
   - Input Format: JSON (use schema from foundry_agent_definition.py)
   - Output Format: JSON (use schema from foundry_agent_definition.py)

4. **Test the Agent**
   - Use Foundry's test interface
   - Sample input from test cases in test_capex_foundry_agent.py
   - Verify response format matches output schema

### Option B: Use via Python Client (Current Implementation)

The CICT-33 Function already uses the Python client:

```python
from autarch.capex.foundry_agent_client import InvoiceTaskDeterminationAgent

agent = InvoiceTaskDeterminationAgent(
    foundry_endpoint="https://cict-ai-cus-foundry.openai.azure.com/openai",
    foundry_api_key=os.environ["FOUNDRY_API_KEY"],
)

task_suggestion = agent.analyze_invoice(
    invoice_package=invoice_data,
    correlation_id="corr-001",
    package_id="pkg-001",
    run_id="run-001",
    invoice_id="INV-001",
)
```

## Integration with CICT-33 Function

The Foundry agent is already integrated into the Invoice Agent Invocation Function:

1. **Current Flow:**
   - Service Bus Message → Function App → Foundry Agent Client → gpt-5.1 Model → SQL

2. **Using Foundry Agent Instead of Direct Model Call:**
   
   Edit `azure_function/function_app.py` to swap:
   
   ```python
   # Before: Direct model call
   from autarch.capex.agent_invocation_handler import invoke_task_determination_agent
   task_suggestion = invoke_task_determination_agent(...)
   
   # After: Use Foundry agent client
   from autarch.capex.foundry_agent_client import InvoiceTaskDeterminationAgent
   agent = InvoiceTaskDeterminationAgent(
       foundry_endpoint=foundry_endpoint,
       foundry_api_key=foundry_api_key,
   )
   task_suggestion = agent.analyze_invoice(...)
   ```

   The response format is identical, so no other changes needed.

## Testing the Agent Locally

```bash
# Run agent tests
python -m pytest tests/test_capex_foundry_agent.py -v

# Expected: 8 tests pass
#  ✓ test_agent_initialization
#  ✓ test_agent_requires_sdk
#  ✓ test_agent_analyzes_invoice
#  ✓ test_agent_parses_plain_json
#  ✓ test_agent_handles_malformed_response
#  ✓ test_agent_definition_is_valid
#  ✓ test_agent_input_schema_valid
#  ✓ test_agent_output_schema_valid
```

## Agent Capabilities

### Input Schema
- **invoice**: Metadata (ID, date, vendor, currency, total)
- **lines**: Invoice line items with descriptions, quantities, prices
- **evidence**: Optional catalogs, policies, approved codes
- **correlation**: IDs for audit trail

### Output Schema
- **per-line recommendations**: CAPEX/OPEX/REVIEW with confidence
- **rationale**: Detailed explanation for each decision
- **evidence_refs**: Sources supporting recommendation
- **policy_ids**: Applicable policies
- **task_code / assetClassCode**: Suggested catalog codes

### Decision Logic
The agent considers:
- Asset type (new asset vs. improvement vs. repair vs. service)
- Useful life (> 1 year = CAPEX)
- Capitalization thresholds
- Directly attributable costs
- Applicable policies

Flags cases as **REVIEW** when:
- Policy is ambiguous or missing
- Data quality issues exist
- Evidence is insufficient
- Multiple interpretations possible

## Post-Demo Enhancements

After the demo, consider:

1. **Multi-turn Conversations** - Use Foundry's session API for complex analysis
2. **Tool Calling** - Access task catalogs, policies, and company coding via tools
3. **Governance Integration** - Use Autarch framework for policy enforcement
4. **Staged Reasoning** - Implement full multi-stage pipeline (7 stages from capex_agents.py)
5. **Model Evolution** - Fine-tune based on historical decisions and review outcomes

## Fallback Strategy

If Foundry agent fails:
- CICT-33 Function automatically falls back to direct gpt-5.1 model call
- Ensures demo continues even if agent deployment delayed
- Both approaches produce identical output format

## Files & Documentation

- **Definition**: `autarch/capex/foundry_agent_definition.py`
- **Client**: `autarch/capex/foundry_agent_client.py`
- **Tests**: `tests/test_capex_foundry_agent.py`
- **Integration**: `azure_function/function_app.py`

## Support

For issues:
1. Check Foundry platform logs: https://cict-ai-cus-foundry.openai.azure.com/logs
2. Review test output: `pytest tests/test_capex_foundry_agent.py -v`
3. Check agent execution trace: Look for `foundry_agent_analysis_*` events in function logs
