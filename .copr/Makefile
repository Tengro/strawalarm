# COPR builds the SRPM by running: make -f .copr/Makefile srpm outdir=...
srpm:
	dnf -y install rpmdevtools
	mkdir -p "$(outdir)"
	spectool -g -C . packaging/strawalarm.spec
	rpmbuild -bs packaging/strawalarm.spec \
	    --define "_sourcedir $(CURDIR)" \
	    --define "_srcrpmdir $(outdir)"
