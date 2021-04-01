# pylint: disable=C0302
"""
Manage VMware vCenter servers and ESXi hosts.

.. versionadded:: 2015.8.4

:codeauthor: Alexandru Bleotu <alexandru.bleotu@morganstaley.com>

Dependencies
============

- pyVmomi Python Module
- ESXCLI

pyVmomi
-------

PyVmomi can be installed via pip:

.. code-block:: bash

    pip install pyVmomi

.. note::

    Version 6.0 of pyVmomi has some problems with SSL error handling on certain
    versions of Python. If using version 6.0 of pyVmomi, Python 2.7.9,
    or newer must be present. This is due to an upstream dependency
    in pyVmomi 6.0 that is not supported in Python versions 2.7 to 2.7.8. If the
    version of Python is not in the supported range, you will need to install an
    earlier version of pyVmomi. See `Issue #29537`_ for more information.

.. _Issue #29537: https://github.com/saltstack/salt/issues/29537

Based on the note above, to install an earlier version of pyVmomi than the
version currently listed in PyPi, run the following:

.. code-block:: bash

    pip install pyVmomi==5.5.0.2014.1.1

The 5.5.0.2014.1.1 is a known stable version that this original vSphere Execution
Module was developed against.

vSphere Automation SDK
----------------------

vSphere Automation SDK can be installed via pip:

.. code-block:: bash

    pip install --upgrade pip setuptools
    pip install --upgrade git+https://github.com/vmware/vsphere-automation-sdk-python.git

.. note::

    The SDK also requires OpenSSL 1.0.1+ if you want to connect to vSphere 6.5+ in order to support
    TLS1.1 & 1.2.

    In order to use the tagging functions in this module, vSphere Automation SDK is necessary to
    install.

The module is currently in version 1.0.3
(as of 8/26/2019)

ESXCLI
------

Currently, about a third of the functions used in the vSphere Execution Module require
the ESXCLI package be installed on the machine running the Proxy Minion process.

The ESXCLI package is also referred to as the VMware vSphere CLI, or vCLI. VMware
provides vCLI package installation instructions for `vSphere 5.5`_ and
`vSphere 6.0`_.

.. _vSphere 5.5: http://pubs.vmware.com/vsphere-55/index.jsp#com.vmware.vcli.getstart.doc/cli_install.4.2.html
.. _vSphere 6.0: http://pubs.vmware.com/vsphere-60/index.jsp#com.vmware.vcli.getstart.doc/cli_install.4.2.html

Once all of the required dependencies are in place and the vCLI package is
installed, you can check to see if you can connect to your ESXi host or vCenter
server by running the following command:

.. code-block:: bash

    esxcli -s <host-location> -u <username> -p <password> system syslog config get

If the connection was successful, ESXCLI was successfully installed on your system.
You should see output related to the ESXi host's syslog configuration.

.. note::

    Be aware that some functionality in this execution module may depend on the
    type of license attached to a vCenter Server or ESXi host(s).

    For example, certain services are only available to manipulate service state
    or policies with a VMware vSphere Enterprise or Enterprise Plus license, while
    others are available with a Standard license. The ``ntpd`` service is restricted
    to an Enterprise Plus license, while ``ssh`` is available via the Standard
    license.

    Please see the `vSphere Comparison`_ page for more information.

.. _vSphere Comparison: https://www.vmware.com/products/vsphere/compare


About
=====

This execution module was designed to be able to handle connections both to a
vCenter Server, as well as to an ESXi host. It utilizes the pyVmomi Python
library and the ESXCLI package to run remote execution functions against either
the defined vCenter server or the ESXi host.

Whether or not the function runs against a vCenter Server or an ESXi host depends
entirely upon the arguments passed into the function. Each function requires a
``host`` location, ``username``, and ``password``. If the credentials provided
apply to a vCenter Server, then the function will be run against the vCenter
Server. For example, when listing hosts using vCenter credentials, you'll get a
list of hosts associated with that vCenter Server:

.. code-block:: bash

    # salt my-minion vsphere.list_hosts <vcenter-ip> <vcenter-user> <vcenter-password>
    my-minion:
    - esxi-1.example.com
    - esxi-2.example.com

However, some functions should be used against ESXi hosts, not vCenter Servers.
Functionality such as getting a host's coredump network configuration should be
performed against a host and not a vCenter server. If the authentication
information you're using is against a vCenter server and not an ESXi host, you
can provide the host name that is associated with the vCenter server in the
command, as a list, using the ``host_names`` or ``esxi_host`` kwarg. For
example:

.. code-block:: bash

    # salt my-minion vsphere.get_coredump_network_config <vcenter-ip> <vcenter-user> \
        <vcenter-password> esxi_hosts='[esxi-1.example.com, esxi-2.example.com]'
    my-minion:
    ----------
        esxi-1.example.com:
            ----------
            Coredump Config:
                ----------
                enabled:
                    False
        esxi-2.example.com:
            ----------
            Coredump Config:
                ----------
                enabled:
                    True
                host_vnic:
                    vmk0
                ip:
                    coredump-location.example.com
                port:
                    6500

You can also use these functions against an ESXi host directly by establishing a
connection to an ESXi host using the host's location, username, and password. If ESXi
connection credentials are used instead of vCenter credentials, the ``host_names`` and
``esxi_hosts`` arguments are not needed.

.. code-block:: bash

    # salt my-minion vsphere.get_coredump_network_config esxi-1.example.com root <host-password>
    local:
    ----------
        10.4.28.150:
            ----------
            Coredump Config:
                ----------
                enabled:
                    True
                host_vnic:
                    vmk0
                ip:
                    coredump-location.example.com
                port:
                    6500
"""
import logging
import sys

import salt.utils.platform
import saltext.vmware.utils.vmware
from salt.exceptions import InvalidConfigError
from salt.utils.decorators import depends
from salt.utils.dictdiffer import recursive_diff
from salt.utils.listdiffer import list_diff
from saltext.vmware.config.schemas.esxvm import ESXVirtualMachineDeleteSchema
from saltext.vmware.config.schemas.esxvm import ESXVirtualMachineUnregisterSchema

log = logging.getLogger(__name__)

try:
    import jsonschema

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

try:
    # pylint: disable=no-name-in-module
    from pyVmomi import (
        vim,
        VmomiSupport,
    )

    # pylint: enable=no-name-in-module

    # We check the supported vim versions to infer the pyVmomi version
    if (
        "vim25/6.0" in VmomiSupport.versionMap
        and sys.version_info > (2, 7)
        and sys.version_info < (2, 7, 9)
    ):

        log.debug("pyVmomi not loaded: Incompatible versions " "of Python. See Issue #29537.")
        raise ImportError()
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

__virtualname__ = "vmware_extra"


def __virtual__():
    return __virtualname__


@depends(HAS_PYVMOMI)
@_supports_proxies("esxi", "esxcluster", "esxdatacenter", "vcenter", "esxvm")
@_gets_service_instance_via_proxy
def test_vcenter_connection(service_instance=None):
    """
    Checks if a connection is to a vCenter

    CLI Example:

    .. code-block:: bash

        salt '*' vsphere.test_vcenter_connection
    """
    try:
        if salt.utils.vmware.is_connection_to_a_vcenter(service_instance):
            return True
    except VMwareSaltError:
        return False
    return False


def _check_hosts(service_instance, host, host_names):
    """
    Helper function that checks to see if the host provided is a vCenter Server or
    an ESXi host. If it's an ESXi host, returns a list of a single host_name.

    If a host reference isn't found, we're trying to find a host object for a vCenter
    server. Raises a CommandExecutionError in this case, as we need host references to
    check against.
    """
    if not host_names:
        host_name = _get_host_ref(service_instance, host)
        if host_name:
            host_names = [host]
        else:
            raise CommandExecutionError(
                "No host reference found. If connecting to a "
                "vCenter Server, a list of 'host_names' must be "
                "provided."
            )
    elif not isinstance(host_names, list):
        raise CommandExecutionError("'host_names' must be a list.")

    return host_names


def _get_date_time_mgr(host_reference):
    """
    Helper function that returns a dateTimeManager object
    """
    return host_reference.configManager.dateTimeSystem


def _get_host_ref(service_instance, host, host_name=None):
    """
    Helper function that returns a host object either from the host location or the host_name.
    If host_name is provided, that is the host_object that will be returned.

    The function will first search for hosts by DNS Name. If no hosts are found, it will
    try searching by IP Address.
    """
    search_index = salt.utils.vmware.get_inventory(service_instance).searchIndex

    # First, try to find the host reference by DNS Name.
    if host_name:
        host_ref = search_index.FindByDnsName(dnsName=host_name, vmSearch=False)
    else:
        host_ref = search_index.FindByDnsName(dnsName=host, vmSearch=False)

    # If we couldn't find the host by DNS Name, then try the IP Address.
    if host_ref is None:
        host_ref = search_index.FindByIp(ip=host, vmSearch=False)

    return host_ref


@depends(HAS_PYVMOMI)
@_supports_proxies("esxi", "esxcluster", "esxdatacenter", "vcenter")
def _get_proxy_target(service_instance):
    """
    Returns the target object of a proxy.

    If the object doesn't exist a VMwareObjectRetrievalError is raised

    service_instance
        Service instance (vim.ServiceInstance) of the vCenter/ESXi host.
    """
    proxy_type = get_proxy_type()
    if not salt.utils.vmware.is_connection_to_a_vcenter(service_instance):
        raise CommandExecutionError(
            "'_get_proxy_target' not supported " "when connected via the ESXi host"
        )
    reference = None
    if proxy_type == "esxcluster":
        (
            host,
            username,
            password,
            protocol,
            port,
            mechanism,
            principal,
            domain,
            datacenter,
            cluster,
        ) = _get_esxcluster_proxy_details()

        dc_ref = salt.utils.vmware.get_datacenter(service_instance, datacenter)
        reference = salt.utils.vmware.get_cluster(dc_ref, cluster)
    elif proxy_type == "esxdatacenter":
        # esxdatacenter proxy
        (
            host,
            username,
            password,
            protocol,
            port,
            mechanism,
            principal,
            domain,
            datacenter,
        ) = _get_esxdatacenter_proxy_details()

        reference = salt.utils.vmware.get_datacenter(service_instance, datacenter)
    elif proxy_type == "vcenter":
        # vcenter proxy - the target is the root folder
        reference = salt.utils.vmware.get_root_folder(service_instance)
    elif proxy_type == "esxi":
        # esxi proxy
        details = __proxy__["esxi.get_details"]()
        if "vcenter" not in details:
            raise InvalidEntityError(
                "Proxies connected directly to ESXi " "hosts are not supported"
            )
        references = salt.utils.vmware.get_hosts(service_instance, host_names=details["esxi_host"])
        if not references:
            raise VMwareObjectRetrievalError(
                "ESXi host '{}' was not found".format(details["esxi_host"])
            )
        reference = references[0]
    log.trace("reference = {}".format(reference))
    return reference


def _get_esxdatacenter_proxy_details():
    """
    Returns the running esxdatacenter's proxy details
    """
    det = __salt__["esxdatacenter.get_details"]()
    return (
        det.get("vcenter"),
        det.get("username"),
        det.get("password"),
        det.get("protocol"),
        det.get("port"),
        det.get("mechanism"),
        det.get("principal"),
        det.get("domain"),
        det.get("datacenter"),
    )


def _get_esxcluster_proxy_details():
    """
    Returns the running esxcluster's proxy details
    """
    det = __salt__["esxcluster.get_details"]()
    return (
        det.get("vcenter"),
        det.get("username"),
        det.get("password"),
        det.get("protocol"),
        det.get("port"),
        det.get("mechanism"),
        det.get("principal"),
        det.get("domain"),
        det.get("datacenter"),
        det.get("cluster"),
    )


def _get_esxi_proxy_details():
    """
    Returns the running esxi's proxy details
    """
    det = __proxy__["esxi.get_details"]()
    host = det.get("host")
    if det.get("vcenter"):
        host = det["vcenter"]
    esxi_hosts = None
    if det.get("esxi_host"):
        esxi_hosts = [det["esxi_host"]]
    return (
        host,
        det.get("username"),
        det.get("password"),
        det.get("protocol"),
        det.get("port"),
        det.get("mechanism"),
        det.get("principal"),
        det.get("domain"),
        esxi_hosts,
    )
