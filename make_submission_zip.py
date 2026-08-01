import shutil
import os

# Source and target setup
root_dir = 'd:/P/agentic_ai'
zip_output_path = 'd:/P/TraceGuard_Project_Submission'

# Exclude unneeded binary / cache / secret files
ignore_pattern = shutil.ignore_patterns(
    'venv', '.venv', '__pycache__', '*.pyc', '.git', '.pytest_cache',
    '*.db', '.env', 'node_modules', 'scratch'
)

temp_dir = 'd:/P/agentic_ai_clean_temp'
if os.path.exists(temp_dir):
    shutil.rmtree(temp_dir)

# Copy clean files
shutil.copytree(root_dir, temp_dir, ignore=ignore_pattern)

# Archive into ZIP
final_zip = shutil.make_archive(zip_output_path, 'zip', temp_dir)

# Cleanup temp dir
shutil.rmtree(temp_dir)

print(f"ZIP file created successfully: {final_zip}")
