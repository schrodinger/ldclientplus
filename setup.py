from setuptools import setup, find_packages

setup(
    name='ldclientplus',
    version='0.1.0',
    packages=find_packages(include=['ldclientplus', 'ldclientplus.*']),
    install_requires=[
        'requests>=2.24.0',
        'six>=1.15.0',
        'urllib3>=1.25.11',
    ],
    setup_requires=['flake8']
)
