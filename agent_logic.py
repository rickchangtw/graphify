"""A2A Agent Logic - Handles TASK_REQUEST messages."""
import sys
import json
def handle_task_request(payload: dict) -> dict:
    """Process a task request and return result."""
    task = payload.get("task", "")
    
    if not task:
        return {"error": "No task provided"}
    
    # Simple task processing
    result = f"Processed task: {task}"
    
    return {
        "status": "completed",
        "task": task,
        "result": result,
        "agent": "sentinel-test-agent",
    }
if __name__ == "__main__":
    # Read task from stdin or arguments
    if len(sys.argv) > 1:
        task_input = sys.argv[1]
    else:
        task_input = input("Enter task: ")
    
    result = handle_task_request({"task": task_input})
    print(json.dumps(result, indent=2))
