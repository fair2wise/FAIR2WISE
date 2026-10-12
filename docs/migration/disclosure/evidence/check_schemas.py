"""Validate T25A schemas, their examples, negative cases, and Pydantic agreement."""
import copy, json, sys
from pathlib import Path
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
import jsonschema
from app.modules.contracts.context import RequestContext
from app.modules.contracts.changes import ApprovalRecord
d = repo / "docs/migration/authentication"
for name, model in (("request_context", RequestContext), ("approval_record", ApprovalRecord)):
    schema = json.loads((d / f"{name}.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    for ex in schema["examples"]:
        errs = list(v.iter_errors(ex)); assert not errs, (name, [e.message for e in errs])
        model.model_validate(ex)
    print(name, "schema ok; examples valid in jsonschema and pydantic")
    # negative: ALS with local-operator / local with issuer
    bad = copy.deepcopy(schema["examples"][0])
    if name == "request_context":
        bad["profile"] = "als-prod"
        cases = [bad]
        b2 = copy.deepcopy(schema["examples"][0]); b2["principal"]["issuer"] = "x"; cases.append(b2)
        b3 = copy.deepcopy(schema["examples"][1]); b3["mode"] = "local_trusted"; cases.append(b3)
    else:
        bad["profile"] = "als-dev"; cases = [bad]
        b2 = copy.deepcopy(schema["examples"][0]); b2["requested_by"] = "someone"; cases.append(b2)
        b3 = copy.deepcopy(schema["examples"][1]); b3["approver_is_admin"] = False; cases.append(b3)
        b4 = copy.deepcopy(schema["examples"][1]); b4["executed_as"] = b4["approved_by"]; cases.append(b4)
    for i, c in enumerate(cases):
        js = v.is_valid(c)
        try:
            model.model_validate(c); py = True
        except Exception:
            py = False
        print(f"  negative {i}: jsonschema_valid={js} pydantic_valid={py}")
