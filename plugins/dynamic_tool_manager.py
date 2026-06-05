import os
import sys
import json
import importlib.util

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")

class DynamicToolManager:
    @staticmethod
    def _ensure_tools_dir():
        if not os.path.exists(TOOLS_DIR):
            os.makedirs(TOOLS_DIR, exist_ok=True)

    @staticmethod
    def get_schemas():
        DynamicToolManager._ensure_tools_dir()
        schemas = []
        for folder_name in os.listdir(TOOLS_DIR):
            folder_path = os.path.join(TOOLS_DIR, folder_name)
            if os.path.isdir(folder_path):
                json_path = os.path.join(folder_path, "tool.json")
                if os.path.exists(json_path):
                    try:
                        with open(json_path, "r", encoding="utf-8") as f:
                            schema = json.load(f)
                            if isinstance(schema, dict) and "name" in schema.get("function", {}):
                                schemas.append(schema)
                    except Exception as e:
                        sys.stderr.write(f"[DynamicToolManager] Failed to load schema {json_path}: {e}\n")
        return schemas

    @staticmethod
    def has_tool(name):
        DynamicToolManager._ensure_tools_dir()
        py_path = os.path.join(TOOLS_DIR, name, "execute.py")
        return os.path.exists(py_path)

    @staticmethod
    def execute(name, args, handler, response):
        DynamicToolManager._ensure_tools_dir()
        py_path = os.path.join(TOOLS_DIR, name, "execute.py")
        if not os.path.exists(py_path):
            from agent_loop import StepOutcome
            yield f"Tool implementation {name}/execute.py not found.\n"
            return StepOutcome(None, next_prompt=f"Tool {name} not found.", should_exit=False)
        
        try:
            spec = importlib.util.spec_from_file_location(f"dynamic_tool_{name}", py_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            if hasattr(mod, "execute"):
                from agent_loop import try_call_generator
                ret = yield from try_call_generator(mod.execute, args, handler, response)
                return ret
            else:
                from agent_loop import StepOutcome
                yield f"Tool {name}/execute.py missing 'execute' function.\n"
                return StepOutcome(None, next_prompt=f"Tool {name} invalid.", should_exit=False)
        except Exception as e:
            from agent_loop import StepOutcome
            yield f"Tool {name} execution failed: {e}\n"
            return StepOutcome(None, next_prompt=f"Tool {name} error: {e}", should_exit=False)
