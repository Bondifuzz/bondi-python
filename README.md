# Bondi

Bondifuzz command line interface implemented in python

## Install and run

Using python 3.7+

```
git clone https://github.com/Bondifuzz/bondi-python.git
pip install --user bondi-python
bondi --help
```

### Code documentation

TODO

### Running tests

TODO

### Spell checking

Download cspell and run to check spell in all sources

```bash
sudo apt install nodejs npm
sudo npm install -g cspell
sudo npm install -g @cspell/dict-ru_ru
cspell link add @cspell/dict-ru_ru
cspell "**/*.{py,md,txt}"
```

### VSCode extensions

- `ms-python.python`
- `ms-python.vscode-pylance`
- `streetsidesoftware.code-spell-checker`
- `streetsidesoftware.code-spell-checker-russian`
