# Installer et lancer le gateway IBKR Web API

Ce document explique, pas à pas, comment mettre en route la connexion aux données IBKR.
**Aucune connaissance préalable requise.** Compter 10–15 min la première fois.

## C'est quoi, et pourquoi ?

Le code ne se connecte pas directement à IBKR : il passe par un **gateway**, un petit
programme qui tourne **sur ta machine**, se connecte à ton compte IBKR, et expose les
données en HTTP local sur `https://localhost:5000`.

```
   Compte IBKR (paper)
        ▲  login (username + mot de passe, via le gateway)
   [ GATEWAY ]   ← petit programme local, écoute sur localhost:5000
        ▲  HTTP local (rien ne sort de ta machine)
   [ Code Python : run_collector.py / scripts/bootstrap.py ]
```

- C'est **le gateway** qui se connecte à ton compte ; **le code Python ne voit jamais ton mot de passe**, il interroge seulement `localhost:5000`.
- On a abandonné **TWS** (l'appli de bureau, instable) pour cette approche, plus stable et plus « API ».

Deux façons de lancer le gateway. **Le code Python est identique pour les deux** (les deux exposent `localhost:5000`) — tu peux commencer par la voie A puis passer à la B plus tard.

---

## Voie A — Client Portal Gateway (Java) — *recommandé pour démarrer*

### A.1 Installer Java
- Télécharger **Temurin JRE/JDK 17+** : https://adoptium.net/ (Windows x64 `.msi`).
- Pendant l'install, cocher **« Set JAVA_HOME »** et **« Add to PATH »**.
- Vérifier dans un nouveau terminal :
  ```powershell
  java -version
  ```
  *(Une version récente comme Java 25 fonctionne aussi ; le gateway affiche des WARNING `sun.misc.Unsafe` inoffensifs.)*

### A.2 Télécharger le gateway
- Zip officiel : https://download2.interactivebrokers.com/portal/clientportal.gw.zip
- **Extraire** (clic droit → « Extraire tout… ») vers un dossier simple, ex. `C:\clientportal.gw`.
- Vérifier que `C:\clientportal.gw\bin\run.bat` et `C:\clientportal.gw\root\conf.yaml` existent.

### A.3 Lancer le gateway
```powershell
cd C:\clientportal.gw
.\bin\run.bat root\conf.yaml
```
- **Laisser ce terminal ouvert** (c'est le gateway qui tourne).
- Quand tu vois `Open https://localhost:5000 to login`, il est prêt.

### A.4 Se connecter (1× par session)
1. Ouvrir un navigateur sur **https://localhost:5000**
2. Avertissement de certificat (normal, certif auto-signé local) → **« Paramètres avancés » → « Continuer vers localhost »**
   *(astuce : sur la page d'erreur, on peut aussi taper au clavier `thisisunsafe`.)*
3. Se connecter avec ses **identifiants paper**.
4. Message **« Client login succeeds »** → c'est bon, on peut fermer l'onglet (la session reste active).

> ⚠️ La session expire après un moment d'inactivité. Le collecteur la maintient en vie
> (`tickle` automatique). Si elle expire quand même : rafraîchir `https://localhost:5000` et se re-loguer.

---

## Voie B — IBeam (Docker) — *automatisé / headless*

Idéal pour faire tourner le gateway sans surveillance (auto-login, auto-restart).

1. Installer **Docker Desktop** (Windows 11 Home : activer WSL2 pendant l'install).
2. Renseigner dans `.env` :
   ```
   IBEAM_ACCOUNT=ton_username_paper
   IBEAM_PASSWORD=ton_mot_de_passe_paper
   ```
3. Lancer :
   ```powershell
   docker compose -f gateway/docker-compose.yml up -d
   docker logs -f ibeam        # attendre l'authentification (~30-60s)
   ```
4. Le gateway écoute sur `https://localhost:5000` (auto-authentifié).
   Arrêt : `docker compose -f gateway/docker-compose.yml down`.

> Les comptes **paper** ne demandent en général pas de 2FA → IBeam peut rester authentifié sans intervention.

---

## Vérifier que tout fonctionne

Gateway lancé et authentifié, puis dans un autre terminal :

```powershell
cd architecture_risque
python scripts/bootstrap.py
```

Sortie attendue : `Session CONNECTED` → `Account id: DU…` → `SPY conid` → snapshot spot →
chaîne d'options → une option ATM avec **greeks/IV** → positions → `Raw event written` → `Heartbeat OK`.

Ensuite, le collecteur :
```powershell
python run_collector.py        # voir docs/runbooks.md
```

---

## Dépannage

| Symptôme | Cause / solution |
|----------|------------------|
| `Votre connexion n'est pas privée` (navigateur) | Normal (certif local). « Continuer vers localhost », ou taper `thisisunsafe`. |
| Le smoke test dit `not authenticated` | Session gateway expirée → rafraîchir `https://localhost:5000` et se reconnecter. |
| `Account id: None` puis positions `401` | Le gateway n'est pas authentifié, ou pas de compte sélectionné → re-login navigateur. |
| Option snapshot vide hors séance | Avant l'ouverture US (15h30 Paris), peu/pas de quotes options ; les greeks-modèle arrivent quand même en général. |
| `WARNING sun.misc.Unsafe` au lancement du gateway | Inoffensif (Java récent). Le gateway tourne quand même. |
| Le gateway ne démarre pas (erreurs Java rouges) | Java trop ancien/incompatible → installer Temurin 17. |
| Login bloqué avec un VPN actif | IBKR est sensible aux VPN → désactiver le VPN du navigateur et réessayer. |
| Les ports 5000 sont pris | Modifier le port dans `root/conf.yaml` (Java) et la section `webapi.port` de `configs/broker.yaml`. |
