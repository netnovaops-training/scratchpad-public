# Deploying this site

## Where the files live

You are building in  `C:\Jasons New Site\www`  on the desktop.
The web server (Caddy + Apache) runs on the **Pi**. So this is build-here,
deploy-there.

    C:\Jasons New Site\www\
      index.php          <- the landing page
      jason\             <- your projects (Apache serves this)
      casey\               <- NOT used: /casey is proxied to the portal app
      mama\              <- NOT used: /mama is proxied to the Tech Help app

Only `index.php` and `jason\` are actual files. `/casey` and `/mama` are handled
by Caddy proxying to the two Python apps -- nothing is served from disk there.

## Push it to the Pi

From PowerShell on the desktop:

    scp -P 1143 -r "C:\Jasons New Site\www\*" person15@example.com:/tmp/site/
    ssh -p 1143 person15@example.com "sudo rsync -a --delete /tmp/site/ /var/www/html/ && sudo chown -R www-data:www-data /var/www/html"

Or, if you set up the ~/.ssh/config entry:

    scp -r "C:\Jasons New Site\www\*" robin:/tmp/site/
    ssh robin "sudo rsync -a --delete /tmp/site/ /var/www/html/ && sudo chown -R www-data:www-data /var/www/html"

## Make Apache serve it

Apache must move off ports 80/443 -- Caddy needs those for TLS.

    sudo nano /etc/apache2/ports.conf
        Listen 127.0.0.1:8080

    sudo nano /etc/apache2/sites-available/000-default.conf
        <VirtualHost 127.0.0.1:8080>
            ServerName example.org
            DocumentRoot /var/www/html
            <Directory /var/www/html>
                Options -Indexes +FollowSymLinks
                AllowOverride All
                Require all granted
                DirectoryIndex index.php index.html
            </Directory>
        </VirtualHost>

    sudo apt install -y php libapache2-mod-php
    sudo a2enmod php*        # tab-complete the version
    sudo apachectl configtest
    sudo systemctl restart apache2
    sudo ss -tlnp | grep -E ':(80|443|8080)'

Then in /etc/caddy/Caddyfile, comment out the `handle { respond ... }` landing
page block and uncomment the Apache one:

    handle {
        reverse_proxy 127.0.0.1:8080
    }

    sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
    sudo systemctl reload caddy

Order matters in the Caddyfile: the /casey and /mama handle_path blocks must come
BEFORE the catch-all `handle`, or Apache swallows everything.

## Check it

    curl -I https://example.org/
    curl -I https://example.org/casey/
    curl -I https://example.org/mama/
