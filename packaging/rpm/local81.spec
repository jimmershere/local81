Name:           local81
Version:        0.1.0
Release:        3%{?dist}
Summary:        Local-81 operator-readable deploy and runbook control plane

License:        MIT
URL:            https://github.com/jimmershere/local81

# Current codebase requires Python 3.12+. On RHEL8 this usually means a custom
# builder/runtime or an internal python3.12 package stream.
BuildRequires:  python3.12
BuildRequires:  python3.12-pip
BuildRequires:  python3.12-setuptools
BuildRequires:  python3.12-wheel
BuildRequires:  python3.12-devel
BuildRequires:  rpm-build
Requires:       /bin/bash
Requires:       python3.12
Requires:       openssh-clients
Requires:       rsync
Requires:       findutils

Source0:        %{name}-%{version}.tar.gz

%global debug_package %{nil}
%global _build_id_links none
%global app_root /opt/local81
%global app_lib %{app_root}/app
%global app_venv %{app_root}/venv

%description
Local-81 is a Python-based deployment and operator control plane for generating
plans, validating deploys, and running controlled workflow operations.

This first RPM scaffold installs Local-81 under /opt/local81, exposes /usr/bin/local81,
and provisions /etc/local81 and /var/lib/local81 for runtime configuration and data.

%prep
%autosetup -n %{name}-%{version}

%build
# Python dependencies are installed into the packaged application virtualenv
# during %%install so the wrapper always runs against /opt/local81/venv.

%install
rm -rf %{buildroot}
install -d %{buildroot}%{app_lib}
cp -a bin docs examples src %{buildroot}%{app_lib}/
install -m 0644 pyproject.toml %{buildroot}%{app_lib}/pyproject.toml
install -m 0644 README.md %{buildroot}%{app_lib}/README.md
install -m 0644 CONTRIBUTING.md %{buildroot}%{app_lib}/CONTRIBUTING.md

python3.12 -m venv --copies %{buildroot}%{app_venv}
%{buildroot}%{app_venv}/bin/python -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
%{buildroot}%{app_venv}/bin/python -m pip install --disable-pip-version-check %{buildroot}%{app_lib}
rm -rf %{buildroot}%{app_lib}/build %{buildroot}%{app_lib}/src/*.egg-info
grep -RIl "%{buildroot}" %{buildroot}%{app_venv} | xargs -r sed -i "s#%{buildroot}##g"

install -d %{buildroot}%{_bindir}
install -m 0755 packaging/common/local81-wrapper %{buildroot}%{_bindir}/local81

install -d %{buildroot}%{_sysconfdir}/local81
install -m 0644 packaging/rpm/local81.ini %{buildroot}%{_sysconfdir}/local81/local81.ini.example

install -d %{buildroot}%{_sharedstatedir}/local81
install -d %{buildroot}%{_docdir}/%{name}
install -m 0644 packaging/rpm/README.md %{buildroot}%{_docdir}/%{name}/
%check
LOCAL81_HOME=%{buildroot}%{app_root} %{buildroot}%{_bindir}/local81 --help >/dev/null
LOCAL81_HOME=%{buildroot}%{app_root} %{buildroot}%{_bindir}/local81 help >/dev/null

%files
%doc %{_docdir}/%{name}/README.md
%dir %{app_root}
%{app_lib}
%{app_venv}
%{_bindir}/local81
%dir %{_sysconfdir}/local81
%config(noreplace) %{_sysconfdir}/local81/local81.ini.example
%dir %{_sharedstatedir}/local81

%changelog
* Wed Jun 24 2026 Local-81 Maintainers <clem@portwright.io> - 0.1.0-3
- Correct license metadata to MIT and set canonical project URL
- Use neutral org maintainer alias (clem@portwright.io)
* Mon Jun 01 2026 Local-81 Maintainers <clem@portwright.io> - 0.1.0-2
- Align RPM application bundle with Debian packaging layout and add smoke checks
* Tue Apr 21 2026 Local-81 Maintainers <clem@portwright.io> - 0.1.0-1
- Initial RPM packaging scaffold
