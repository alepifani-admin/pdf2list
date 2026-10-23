# Componenti di terze parti

PDF2LIST usa i seguenti componenti open source. Le rispettive licenze si
applicano ai componenti, non al codice originale di PDF2LIST (MIT).

| Componente | Licenza | Note |
|---|---|---|
| PyMuPDF (MuPDF) | AGPL-3.0 o licenza commerciale Artifex | **Attenzione**: se distribuisci l'app come prodotto chiuso, valuta una licenza commerciale Artifex oppure la sostituzione del componente. |
| PySide6 / Qt | LGPL-3.0 (con eccezioni Qt) | Le librerie Qt sono incluse dinamicamente nel bundle. |
| RapidOCR | Apache-2.0 | Motore OCR e modelli PaddleOCR. |
| ONNX Runtime | MIT | Esecuzione dei modelli OCR. |
| OpenCV | Apache-2.0 | Preprocessing OCR. |
| openpyxl | MIT | Scrittura file Excel. |
| NumPy | BSD-3-Clause | Calcolo numerico. |
| Pillow | HPND / PIL | Gestione immagini. |
| keyring | MIT | Portachiavi di sistema. |

Il listino PDF di esempio (`source.pdf`) e i file derivati presenti nella
cartella appartengono ai rispettivi titolari e sono inclusi solo come esempio
tecnico.
