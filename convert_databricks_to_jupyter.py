#!/usr/bin/env python3
"""
Convert Databricks notebooks (Python source format) to Jupyter notebooks (.ipynb format).

This script converts .py files that contain Databricks notebook source code into
standard Jupyter notebook format that can be opened and edited in Jupyter or VS Code.
"""

import json
import re
import os
import argparse
from pathlib import Path
from typing import List, Dict, Tuple


def parse_databricks_notebook(content: str) -> List[Tuple[str, str, str]]:
    """
    Parse a Databricks notebook in Python source format.
    
    Returns a list of tuples: (cell_type, cell_title, cell_content)
    """
    cells = []
    
    # Split by COMMAND separator
    commands = re.split(r'^# COMMAND ----------\s*$', content, flags=re.MULTILINE)
    
    for command in commands:
        if not command.strip():
            continue
            
        # Extract DBTITLE if present
        title_match = re.match(r'^# DBTITLE \d+,(.*?)$', command.strip(), re.MULTILINE)
        title = title_match.group(1).strip() if title_match else ""
        
        # Remove DBTITLE line if present
        if title_match:
            command = re.sub(r'^# DBTITLE \d+,.*?$', '', command, count=1, flags=re.MULTILINE)
        
        # Check if this is a magic command cell
        magic_lines = []
        code_lines = []
        
        for line in command.split('\n'):
            if line.strip().startswith('# MAGIC'):
                # Remove '# MAGIC ' prefix
                magic_content = re.sub(r'^# MAGIC\s?', '', line)
                magic_lines.append(magic_content)
            else:
                code_lines.append(line)
        
        # Determine cell type and content
        if magic_lines and not any(line.strip() and not line.strip().startswith('#') for line in code_lines):
            # This is a magic/markdown cell
            content = '\n'.join(magic_lines)
            
            # Check if it's actually markdown (starts with %md or similar)
            if content.strip().startswith(('%md', '%markdown')):
                # Remove the %md directive
                content = re.sub(r'^%m(d|arkdown)\s*', '', content, flags=re.MULTILINE)
                cell_type = 'markdown'
            else:
                cell_type = 'code'
        else:
            # This is a code cell
            content = '\n'.join(code_lines)
            cell_type = 'code'
            
            # If there were magic commands mixed with code, prepend them back
            if magic_lines:
                content = '\n'.join(magic_lines) + '\n' + content
        
        # Clean up the content
        content = content.strip()
        
        if content or title:
            cells.append((cell_type, title, content))
    
    return cells


def create_jupyter_notebook(cells: List[Tuple[str, str, str]]) -> Dict:
    """
    Create a Jupyter notebook dictionary from parsed cells.
    """
    notebook = {
        "cells": [],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3"
            },
            "language_info": {
                "name": "python",
                "version": "3.10.0",
                "mimetype": "text/x-python",
                "codemirror_mode": {
                    "name": "ipython",
                    "version": 3
                },
                "pygments_lexer": "ipython3",
                "nbconvert_exporter": "python",
                "file_extension": ".py"
            }
        },
        "nbformat": 4,
        "nbformat_minor": 4
    }
    
    for cell_type, title, content in cells:
        # Add title as markdown if present
        if title:
            notebook["cells"].append({
                "cell_type": "markdown",
                "metadata": {},
                "source": [f"### {title}\n"]
            })
        
        # Add the cell content
        if content:
            # Split content by newlines and add '\n' to each line except the last
            lines = content.split('\n')
            source_lines = [line + '\n' for line in lines[:-1]]
            if lines[-1]:  # Add last line without '\n' if it's not empty
                source_lines.append(lines[-1])
            
            cell = {
                "cell_type": cell_type,
                "metadata": {},
                "source": source_lines
            }
            
            if cell_type == "code":
                cell["execution_count"] = None
                cell["outputs"] = []
            
            notebook["cells"].append(cell)
    
    return notebook


def convert_file(input_path: Path, output_path: Path) -> None:
    """
    Convert a single Databricks notebook file to Jupyter format.
    """
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if this is a Databricks notebook
    if not content.startswith('# Databricks notebook source'):
        print(f"Warning: {input_path} doesn't appear to be a Databricks notebook. Skipping.")
        return
    
    # Parse the notebook
    cells = parse_databricks_notebook(content)
    
    # Create Jupyter notebook
    notebook = create_jupyter_notebook(cells)
    
    # Write to output file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(notebook, f, indent=2, ensure_ascii=False)
    
    print(f"Converted: {input_path} -> {output_path}")


def find_databricks_notebooks(directory: Path) -> List[Path]:
    """
    Find all Databricks notebook files in a directory.
    """
    notebooks = []
    
    for py_file in directory.rglob('*.py'):
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                if first_line.strip() == '# Databricks notebook source':
                    notebooks.append(py_file)
        except Exception as e:
            print(f"Error reading {py_file}: {e}")
    
    return notebooks


def main():
    parser = argparse.ArgumentParser(
        description='Convert Databricks notebooks to Jupyter format'
    )
    parser.add_argument(
        'input',
        help='Input file or directory containing Databricks notebooks'
    )
    parser.add_argument(
        '-o', '--output',
        help='Output directory for converted notebooks (default: creates .ipynb alongside .py files)'
    )
    parser.add_argument(
        '-r', '--recursive',
        action='store_true',
        help='Recursively search for notebooks in subdirectories'
    )
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    
    if input_path.is_file():
        # Convert single file
        if args.output:
            output_path = Path(args.output)
        else:
            output_path = input_path.with_suffix('.ipynb')
        convert_file(input_path, output_path)
    
    elif input_path.is_dir():
        # Convert all notebooks in directory
        notebooks = find_databricks_notebooks(input_path)
        print(f"Found {len(notebooks)} Databricks notebooks")
        
        for notebook_path in notebooks:
            if args.output:
                # Maintain directory structure in output
                rel_path = notebook_path.relative_to(input_path)
                output_path = Path(args.output) / rel_path.with_suffix('.ipynb')
            else:
                # Create .ipynb alongside .py file
                output_path = notebook_path.with_suffix('.ipynb')
            
            convert_file(notebook_path, output_path)
    
    else:
        print(f"Error: {input_path} is not a valid file or directory")
        return 1
    
    return 0


if __name__ == '__main__':
    exit(main())

