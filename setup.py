# setup.py

from setuptools import setup, find_packages

setup(
    name="openstack-vm-balancer",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "python-openstackclient",
        "requests",
    ],
    entry_points={
        'console_scripts': [
            'balance-vms=openstack_balancer.cli:main',
        ],
    },
    author="Your Name",
    author_email="your.email@example.com",
    description="OpenStack VM load balancing tool",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    keywords="openstack virtualization load-balancing",
    python_requires=">=3.6",
)
