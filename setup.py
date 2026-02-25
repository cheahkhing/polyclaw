from setuptools import setup, find_packages

setup(
    name="polyclaw",
    version="0.1.0",
    description="Polymarket toolkit for OpenClaw agents",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "py-clob-client>=0.34.0",
        "requests>=2.28.0",
        "websockets>=12.0",
        "aiohttp>=3.9.0",
        "click>=8.0",
        "python-dotenv>=1.0",
        "rich>=13.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "responses>=0.23",
            "pytest-mock>=3.12",
        ],
    },
    entry_points={
        "console_scripts": [
            "polyclaw=polyclaw.cli:main",
        ],
    },
)
