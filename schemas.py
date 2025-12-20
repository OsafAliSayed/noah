from pydantic import BaseModel, Field
from typing import Optional



class ModelOutputFormat(BaseModel):
    """
    This model is used for output formatting of ChatGPT.
    """
    step: str = Field(..., description="The ID of the step. Example: PLAN, OUTPUT, TOOL, etc")
    content: Optional[str] = Field(None, description="The optional string content for the step")
    tool: Optional[str] = Field(None, description="The ID of the tool to call.")
    input: Optional[str] = Field(None, description="The input params for the tool")