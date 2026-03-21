FROM httpd:2.4-alpine

RUN apk add --no-cache apr-util-dbm_gdbm

COPY httpd.conf /usr/local/apache2/conf/httpd.conf 
COPY user.passwd /usr/local/apache2/user.passwd
COPY httpd-dav.conf /usr/local/apache2/conf/extra/httpd-dav.conf

EXPOSE 80

RUN mkdir -p /usr/local/apache2/var && mkdir -p /usr/local/apache2/uploads

RUN chown -R www-data:www-data /usr/local/apache2/var /usr/local/apache2/uploads


