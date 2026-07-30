"""Regression checks for the hand-maintained Alexandra system prompt."""

import ast
import hashlib
from pathlib import Path
import unittest


EXPECTED_SYSTEM_PROMPT_SHA256 = "eb02129a6a381f8ffe10ba9e77b5512746d5053876568e6f139d2a6ea48c87d7"


def read_system_instruction_literal() -> str:
    source = Path("bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SYSTEM_INSTRUCTION"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("SYSTEM_INSTRUCTION literal not found")


class SystemPromptPreservationTests(unittest.TestCase):
    def test_business_system_prompt_remains_byte_for_byte_intact(self):
        prompt = read_system_instruction_literal()
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        self.assertEqual(digest, EXPECTED_SYSTEM_PROMPT_SHA256)


if __name__ == "__main__":
    unittest.main()
