from pathlib import Path


def test_github_recovery_workflow_is_manual_confirmed_and_brand_isolated():
    workflow = (Path(__file__).parents[1] / ".github/workflows/catalog-delivery-recovery.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "environment: ${{ inputs.deployment }}" in workflow
    assert "RECOVERY_BACKEND_URL: ${{ secrets.RECOVERY_BACKEND_URL }}" in workflow
    assert "DASHBOARD_API_KEY: ${{ secrets.DASHBOARD_API_KEY }}" in workflow
    assert "BUSINESS_ID: ${{ vars.BUSINESS_ID }}" in workflow
    assert '!= "REENVIAR"' in workflow
    assert 'phones.upper() == "ALL"' in workflow
