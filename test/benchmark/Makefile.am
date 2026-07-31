

noinst_PROGRAMS = benchmark intervention_log_benchmark

benchmark_SOURCES = \
        benchmark.cc

benchmark_LDADD = \
	$(CURL_LDADD) \
	$(GEOIP_LDADD) \
	$(MAXMIND_LDADD) \
	$(PCRE_LDADD) \
	$(PCRE2_LDADD) \
	$(YAJL_LDADD) \
	$(LMDB_LDADD) \
	$(SSDEEP_LDADD) \
	$(LUA_LDADD) \
	$(LIBXML2_LDADD) \
	$(GLOBAL_LDADD)

benchmark_LDFLAGS = \
	-L$(top_builddir)/src/.libs/ \
	$(GEOIP_LDFLAGS) \
	-lmodsecurity \
	-lpthread \
	-lm \
	-lstdc++ \
	$(GEOIP_LDFLAGS) \
	$(MAXMIND_LDFLAGS) \
	$(YAJL_LDFLAGS) \
	$(LMDB_LDFLAGS) \
	$(SSDEEP_LDFLAGS) \
	$(LUA_LDFLAGS)

benchmark_CPPFLAGS = \
	-I$(top_builddir)/headers \
	$(GLOBAL_CPPFLAGS) \
	$(PCRE_CFLAGS) \
	$(PCRE2_CFLAGS) \
	$(LMDB_CFLAGS) \
	$(LIBXML2_CFLAGS)

intervention_log_benchmark_SOURCES = \
	intervention_log_benchmark.cc

intervention_log_benchmark_LDADD = \
	$(benchmark_LDADD)

intervention_log_benchmark_LDFLAGS = \
	$(benchmark_LDFLAGS)

intervention_log_benchmark_CPPFLAGS = \
	$(benchmark_CPPFLAGS)

MAINTAINERCLEANFILES = \
        Makefile.in

