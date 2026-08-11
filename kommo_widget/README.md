# Widget de Salesbot para Kommo

El archivo que debe cargarse en la Integración Privada se genera automáticamente
como un **artefacto de GitHub Actions**:

```text
kommo-salesbot-widget.zip
```

## Descargar desde GitHub (sin terminal)

El ZIP no se guarda como binario dentro de la rama porque Codex Cloud no permite
crear un PR que contenga binarios. En su lugar, GitHub lo construye desde los archivos
de texto verificables del widget:

1. Fusiona el PR en GitHub.
2. Abre la pestaña **Actions** del repositorio.
3. En la columna izquierda selecciona **Build Kommo widget**.
4. Abre la ejecución verde correspondiente al commit fusionado. Si no hay una,
   pulsa **Run workflow**, selecciona la rama y confirma **Run workflow**.
5. Baja hasta **Artifacts** y descarga **kommo-salesbot-widget**.
6. GitHub descargará `kommo-salesbot-widget.zip`. Ese archivo ya es el ZIP instalable:
   no lo descomprimas ni lo vuelvas a comprimir.
7. Antes de subirlo, puedes abrirlo sin extraerlo para comprobar que `manifest.json`
   aparece inmediatamente junto a `script.js`, `i18n/` e `images/`.

## Subirlo a la integración mostrada en Kommo

1. Entra a **Ajustes → Centro de integraciones**.
2. Abre **Agente AI Railway 2** y pulsa **Editar**.
3. En **Integración con código personalizado**, pulsa **Subir**.
4. Selecciona `kommo-salesbot-widget.zip` y pulsa **Guardar**.
5. Recarga Kommo completamente (`Ctrl+F5`).
6. Abre o crea el Salesbot, añade un paso **Widget** y selecciona **Agente IA Railway**.
7. En **URL del webhook de Railway** escribe la URL completa del servicio web:

   ```text
   https://TU-DOMINIO.up.railway.app/api/webhook/kommo
   ```

8. Guarda y publica el Salesbot.

## Estructura exacta del ZIP

Al abrir el ZIP, `manifest.json` debe verse inmediatamente en la raíz. **No debe
existir una carpeta `kommo_widget` envolviendo los archivos.**

```text
kommo-salesbot-widget.zip
├── manifest.json
├── script.js
├── i18n/
│   ├── en.json
│   └── es.json
└── images/
    ├── logo.png
    ├── logo_main.png
    ├── logo_medium.png
    ├── logo_min.png
    └── logo_small.png
```

GitHub ejecuta este mismo comando para construir el artefacto. También puede usarse
localmente después de modificar el widget:

```bash
./kommo_widget/build.sh
```

## Variables adicionales en Railway

Genera la **Clave secreta** y el **Token de larga duración** en la pestaña
**Llaves y alcances** de la integración y configura:

```text
KOMMO_BASE_URL=https://tanakasaludablecali.kommo.com
KOMMO_PRIVATE_TOKEN=<token de larga duración>
KOMMO_INTEGRATION_SECRET=<clave secreta generada>
KOMMO_INTEGRATION_ID=195b0635-616f-4cec-9c30-6263133a9d21
```

No publiques la clave ni el token en GitHub. El backend usa la clave para verificar
el JWT de un solo uso que Kommo adjunta a cada `widget_request`.
