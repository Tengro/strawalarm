Name:           strawalarm
Version:        0.11.0
Release:        1%{?dist}
Summary:        Sleep timer and music alarm for MPRIS2 media players
License:        MIT
URL:            https://github.com/Tengro/strawalarm
Source0:        %{url}/archive/v%{version}/%{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  systemd-rpm-macros

Recommends:     playerctl
Requires:       python3-gobject
Requires:       python3-pyside6
Recommends:     libnotify

%description
Straw Alarm — fall asleep to your music, wake up to your playlist.
An AIMP-style sleep timer (fade-out; stop after a duration, a clock
time or N tracks) and music alarm (RTC wake-from-suspend, fade-in,
snooze, weekday recurrence, phone remote control via KDE Connect) for
any MPRIS2 player: Strawberry, VLC, Elisa and friends. Includes a Qt
GUI, a scriptable CLI and an optional background daemon that keeps
alarms alive across crashes.

%prep
%autosetup

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files strawalarm
install -Dm644 data/strawalarm.desktop \
    %{buildroot}%{_datadir}/applications/strawalarm.desktop
install -Dm644 src/strawalarm/data/strawalarm.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/strawalarm.svg
install -Dm644 data/io.github.tengro.strawalarm.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.github.tengro.strawalarm.metainfo.xml
# the shipped unit points at ~/.local/bin (install.sh layout); RPM
# installs the entry point in %%{_bindir}
sed 's|%%h/.local/bin/strawalarmd|%{_bindir}/strawalarmd|' \
    data/strawalarmd.service > strawalarmd.service.rpm
install -Dm644 strawalarmd.service.rpm \
    %{buildroot}%{_userunitdir}/strawalarmd.service

%post
%systemd_user_post strawalarmd.service

%preun
%systemd_user_preun strawalarmd.service

%files -f %{pyproject_files}
%license LICENSE
%doc README.md CHANGELOG.md
%{_bindir}/strawalarm
%{_bindir}/strawalarm-gui
%{_bindir}/strawalarmd
%{_datadir}/applications/strawalarm.desktop
%{_datadir}/icons/hicolor/scalable/apps/strawalarm.svg
%{_metainfodir}/io.github.tengro.strawalarm.metainfo.xml
%{_userunitdir}/strawalarmd.service

%changelog
* Fri Jul 10 2026 Tengro <tengro@gmail.com> - 0.10.0-1
- Initial package: Straw Alarm 0.10.0 (daemon release)
