"""Pass 1: Preview generation prompts."""

PASS1_SYSTEM = """You are an expert RTL design analyst.
Review the structural information of a Verilog module and produce a concise preview.
Focus on the module's role in the hardware hierarchy, its primary function, and its key interface relationships with surrounding modules.
Use precise, professional, and efficient technical English. Keep the wording compact, avoid decorative phrasing, and do not omit important items with vague terms such as "etc."
The output must be strictly limited to one single paragraph.
"""

PASS1_PROMPT = """
# Module Name:
{module_name}

# Parent Module Preview:
{ancestor_context}

# Port List:
{port_summary}

# Submodule List:
{children_summary}

# Structured Circuit Description:
{graph_description}

Please generate a 1-paragraph preview for this module based on the information above.
"""
