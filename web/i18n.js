/* Niibot Studio — tiny gettext-style i18n layer.
 *
 * Source strings in the code are English. DICT maps an English string to its
 * translation; anything missing simply stays English. Text nodes and the
 * title/placeholder/aria-label attributes are translated automatically (also for
 * content rendered later by app.js), and the language is remembered per browser.
 */
(function () {
  'use strict';

  const LOCALES = [
    { code: 'en', flag: '🇬🇧', label: 'English', tag: 'en' },
    { code: 'pl', flag: '🇵🇱', label: 'Polski', tag: 'pl' },
    { code: 'de', flag: '🇩🇪', label: 'Deutsch', tag: 'de' },
    { code: 'fr', flag: '🇫🇷', label: 'Français', tag: 'fr' },
    { code: 'es', flag: '🇪🇸', label: 'Español', tag: 'es' },
    { code: 'it', flag: '🇮🇹', label: 'Italiano', tag: 'it' },
  ];

  const DICT = {
    pl: {
      'Printer queue': 'Kolejka druku',
      File: 'Plik', Edit: 'Edycja', View: 'Widok', Settings: 'Ustawienia', Help: 'Pomoc',
      Ready: 'Gotowe', 'Not Ready': 'Brak gotowości', 'Checking…': 'Sprawdzam…',
      Language: 'Język', 'not connected': 'nie połączono', 'Connection state': 'Stan połączenia',
      Design: 'Projekt', Sheet: 'Arkusz', 'Work area view': 'Widok obszaru roboczego',
      Properties: 'Właściwości', Layers: 'Warstwy', 'Select an element on the label to edit it.':
        'Wybierz element na etykiecie, aby go edytować.',
      Row: 'Wiersz', Column: 'Kolumna', Import: 'Import', Clear: 'Wyczyść', 'Print sheet': 'Drukuj arkusz',
      Mirror: 'Mirror', flip: 'odbicie', 'Sort…': 'Sortuj…', 'Sort direction': 'Kierunek sortowania',
      'Sort rows by column': 'Sortuj wiersze według kolumny',
      'Fill the remaining label regions with a copy of the first one':
        'Wypełnij pozostałe regiony etykiety kopią pierwszego',
      'Second region as a vertical flip instead of a copy':
        'Drugi region jako odbicie pionowe zamiast kopii',
      'Name (mm)': 'Nazwa (mm)', 'Font': 'Czcionka', 'Size (pt)': 'Rozmiar (pt)',
      'Line height': 'Interlinia', 'Align': 'Wyrównanie', 'Vertical': 'Pion',
      'Bold': 'Pogrubienie', 'Italic': 'Kursywa', 'Auto fit': 'Dopasuj',
      'Rotation': 'Obrót', 'Text': 'Treść', 'Data': 'Dane', 'Format': 'Format',
      'Text under code': 'Tekst pod kodem', 'Error correction': 'Korekcja błędów',
      'Line width (mm)': 'Grubość linii (mm)', 'Fill': 'Wypełnij', 'Thickness (mm)': 'Grubość (mm)',
      'Image': 'Obraz', 'Pick image': 'Wybierz obraz', 'Change image': 'Zmień obraz',
      'Print': 'Drukuj', 'Test print': 'Wydruk testowy', 'Save label': 'Zapisz etykietę',
      Copies: 'Kopie', 'Print the sheet': 'Drukuj arkusz',
      Orientation: 'Orientacja', 'Label orientation': 'Orientacja etykiety',
      'Rotate design 90° right': 'Obróć projekt o 90° w prawo',
      'Rotate design 90° left': 'Obróć projekt o 90° w lewo',
      'Swap the label only (keep the design)': 'Zamień tylko wymiary (zostaw projekt)',
      Density: 'Gęstość', faintest: 'najjaśniej', lighter: 'jaśniej', standard: 'standardowo',
      darker: 'ciemniej', darkest: 'najciemniej', 'Dither photo': 'Rasteryzacja zdjęcia',
      'Save label as…': 'Zapisz etykietę jako…', 'Save as template…': 'Zapisz jako szablon…',
      'New label': 'Nowa etykieta', 'Load label…': 'Wczytaj etykietę…', 'Export PNG': 'Eksport PNG',
      'Close window': 'Zamknij okno', Undo: 'Cofnij', Redo: 'Ponów', 'Duplicate element': 'Duplikuj element',
      'Delete element': 'Usuń element', 'Center horizontally': 'Wyśrodkuj poziomo',
      'Center vertically': 'Wyśrodkuj pionowo', 'Insert icon…': 'Wstaw ikonę…', 'Font…': 'Czcionka…',
      'Grid on label': 'Siatka na etykiecie', 'Snap to grid': 'Przyciąganie do siatki',
      'Print regions': 'Regiony wydruku', 'Detect roll format': 'Wykryj format rolki',
      'Refresh printer state': 'Odśwież stan drukarki', 'Paper & roll…': 'Papier i rolka…',
      'Print from Windows…': 'Wydruk z Windows…', 'Import NIIMBOT catalogue': 'Importuj katalog NIIMBOT',
      'Statistics…': 'Statystyki…', 'About…': 'O programie…', 'Documentation (README)': 'Dokumentacja (README)',
      Printer: 'Drukarka', 'Paper & roll': 'Papier i rolka', 'Windows print': 'Wydruk z Windows',
      'Format catalogue': 'Katalog formatów', Statistics: 'Statystyki', About: 'O programie',
      Connection: 'Połączenie', Scan: 'Skanuj', Refresh: 'Odśwież', Disconnect: 'Rozłącz',
      Model: 'Model', Service: 'Serwis', 'Paper calibration': 'Kalibracja papieru',
      'Line 1 is the current label — editing it updates the design.':
        'Wiersz 1 to bieżąca etykieta — edycja działa od razu na projekcie.',
      'Add row': 'Dodaj wiersz', 'Add column': 'Dodaj kolumnę',
      Cancel: 'Anuluj', Save: 'Zapisz', 'Save as copy': 'Zapisz jako kopię', 'Save template': 'Zapisz szablon',
      'Label name': 'Nazwa etykiety', 'Template name': 'Nazwa szablonu',
      'Show all sizes': 'Pokaż wszystkie rozmiary', 'Icons & cliparts': 'Ikony i cliparty',
      Fonts: 'Czcionki', All: 'Wszystkie', 'Search fonts…': 'Szukaj czcionki…',
      'Search icons (e.g. coffee, arrow, user…)': 'Szukaj ikony (np. coffee, arrow, user…)',
      'No layers yet': 'Brak warstw', 'Roll size saved': 'Rozmiar rolki zapisany',
      Barcode: 'Kod kreskowy', 'Ready-made size': 'Gotowy rozmiar', 'Blank canvas': 'Puste płótno',
      'fits roll': 'pasuje do rolki', 'no data yet': 'brak danych',
    },
    de: {
      'Printer queue': 'Druckwarteschlange',
      Ready: 'Bereit', 'Not Ready': 'Nicht bereit', 'Checking?': 'Pr?fe?',
      File: 'Datei', Edit: 'Bearbeiten', View: 'Ansicht', Settings: 'Einstellungen', Help: 'Hilfe',
      Language: 'Sprache', 'not connected': 'nicht verbunden', 'Connection state': 'Verbindungsstatus',
      Design: 'Design', Sheet: 'Tabelle', 'Work area view': 'Arbeitsbereich',
      Properties: 'Eigenschaften', Layers: 'Ebenen',
      'Select an element on the label to edit it.': 'Wähle ein Element auf dem Etikett zum Bearbeiten.',
      Row: 'Zeile', Column: 'Spalte', Import: 'Importieren', Clear: 'Leeren',
      'Print sheet': 'Tabelle drucken', Mirror: 'Spiegeln', flip: 'spiegeln',
      'Sort…': 'Sortieren…', 'Sort direction': 'Sortierrichtung',
      'Sort rows by column': 'Zeilen nach Spalte sortieren',
      'Name (mm)': 'Name (mm)', Font: 'Schrift', 'Size (pt)': 'Größe (pt)', 'Line height': 'Zeilenabstand',
      Align: 'Ausrichtung', Vertical: 'Vertikal', Bold: 'Fett', Italic: 'Kursiv', 'Auto fit': 'Anpassen',
      Rotation: 'Drehung', Text: 'Text', Data: 'Daten', Format: 'Format',
      'Text under code': 'Text unter dem Code', 'Error correction': 'Fehlerkorrektur',
      'Line width (mm)': 'Linienstärke (mm)', Fill: 'Füllen', 'Thickness (mm)': 'Stärke (mm)',
      Image: 'Bild', 'Pick image': 'Bild wählen', 'Change image': 'Bild ändern',
      Print: 'Drucken', 'Test print': 'Testdruck', 'Save label': 'Etikett speichern',
      Copies: 'Kopien', 'Print the sheet': 'Tabelle drucken',
      Orientation: 'Ausrichtung', 'Label orientation': 'Etikett-Ausrichtung',
      'Rotate design 90° right': 'Design 90° nach rechts drehen',
      'Rotate design 90° left': 'Design 90° nach links drehen',
      'Swap the label only (keep the design)': 'Nur Maße tauschen (Design behalten)',
      Density: 'Dichte', faintest: 'sehr hell', lighter: 'heller', standard: 'standard',
      darker: 'dunkler', darkest: 'am dunkelsten', 'Dither photo': 'Foto dithern',
      'Save label as…': 'Etikett speichern unter…', 'Save as template…': 'Als Vorlage speichern…',
      'New label': 'Neues Etikett', 'Load label…': 'Etikett laden…', 'Export PNG': 'PNG exportieren',
      'Close window': 'Fenster schließen', Undo: 'Rückgängig', Redo: 'Wiederholen',
      'Duplicate element': 'Element duplizieren', 'Delete element': 'Element löschen',
      'Center horizontally': 'Horizontal zentrieren', 'Center vertically': 'Vertikal zentrieren',
      'Insert icon…': 'Symbol einfügen…', 'Font…': 'Schrift…',
      'Grid on label': 'Raster auf dem Etikett', 'Snap to grid': 'Am Raster einrasten',
      'Print regions': 'Druckbereiche', 'Detect roll format': 'Rollenformat erkennen',
      'Refresh printer state': 'Druckerstatus aktualisieren', 'Paper & roll…': 'Papier & Rolle…',
      'Print from Windows…': 'Druck aus Windows…', 'Import NIIMBOT catalogue': 'NIIMBOT-Katalog importieren',
      'Statistics…': 'Statistik…', 'About…': 'Info…', 'Documentation (README)': 'Dokumentation (README)',
      Printer: 'Drucker', 'Paper & roll': 'Papier & Rolle', 'Windows print': 'Windows-Druck',
      'Format catalogue': 'Formatkatalog', Statistics: 'Statistik', About: 'Info',
      Connection: 'Verbindung', Scan: 'Suchen', Refresh: 'Aktualisieren', Disconnect: 'Trennen',
      Model: 'Modell', Service: 'Service', 'Paper calibration': 'Papier kalibrieren',
      Cancel: 'Abbrechen', Save: 'Speichern', 'Save as copy': 'Als Kopie speichern',
      'Save template': 'Vorlage speichern', 'Label name': 'Etikettname', 'Template name': 'Vorlagenname',
      'Show all sizes': 'Alle Größen zeigen', 'Icons & cliparts': 'Symbole & Cliparts',
      Fonts: 'Schriften', All: 'Alle', 'Search fonts…': 'Schrift suchen…',
      'Search icons (e.g. coffee, arrow, user…)': 'Symbol suchen (z. B. coffee, arrow, user…)',
      'No layers yet': 'Noch keine Ebenen', 'Roll size saved': 'Rollengröße gespeichert',
      Barcode: 'Barcode', 'Ready-made size': 'Fertige Größe', 'Blank canvas': 'Leere Fläche',
      'fits roll': 'passt zur Rolle', 'no data yet': 'noch keine Daten',
    },
    fr: {
      'Printer queue': 'File d\'impression',
      Ready: 'Pr?t', 'Not Ready': 'Indisponible', 'Checking?': 'V?rification?',
      File: 'Fichier', Edit: 'Édition', View: 'Affichage', Settings: 'Paramètres', Help: 'Aide',
      Language: 'Langue', 'not connected': 'non connecté', 'Connection state': 'État de connexion',
      Design: 'Projet', Sheet: 'Feuille', 'Work area view': 'Zone de travail',
      Properties: 'Propriétés', Layers: 'Calques',
      'Select an element on the label to edit it.': 'Sélectionnez un élément de l’étiquette à modifier.',
      Row: 'Ligne', Column: 'Colonne', Import: 'Importer', Clear: 'Effacer',
      'Print sheet': 'Imprimer la feuille', Mirror: 'Miroir', flip: 'miroir',
      'Sort…': 'Trier…', 'Sort direction': 'Sens du tri', 'Sort rows by column': 'Trier les lignes par colonne',
      'Name (mm)': 'Nom (mm)', Font: 'Police', 'Size (pt)': 'Taille (pt)', 'Line height': 'Interligne',
      Align: 'Alignement', Vertical: 'Vertical', Bold: 'Gras', Italic: 'Italique', 'Auto fit': 'Ajuster',
      Rotation: 'Rotation', Text: 'Texte', Data: 'Données', Format: 'Format',
      'Text under code': 'Texte sous le code', 'Error correction': 'Correction d’erreur',
      'Line width (mm)': 'Épaisseur du trait (mm)', Fill: 'Remplir', 'Thickness (mm)': 'Épaisseur (mm)',
      Image: 'Image', 'Pick image': 'Choisir une image', 'Change image': 'Changer l’image',
      Print: 'Imprimer', 'Test print': 'Impression test', 'Save label': 'Enregistrer l’étiquette',
      Copies: 'Copies', 'Print the sheet': 'Imprimer la feuille',
      Orientation: 'Orientation', 'Label orientation': 'Orientation de l’étiquette',
      'Rotate design 90° right': 'Pivoter le design de 90° à droite',
      'Rotate design 90° left': 'Pivoter le design de 90° à gauche',
      'Swap the label only (keep the design)': 'Inverser seulement les dimensions',
      Density: 'Densité', faintest: 'très clair', lighter: 'plus clair', standard: 'standard',
      darker: 'plus foncé', darkest: 'très foncé', 'Dither photo': 'Tramer la photo',
      'Save label as…': 'Enregistrer l’étiquette sous…', 'Save as template…': 'Enregistrer comme modèle…',
      'New label': 'Nouvelle étiquette', 'Load label…': 'Charger une étiquette…', 'Export PNG': 'Exporter PNG',
      'Close window': 'Fermer la fenêtre', Undo: 'Annuler', Redo: 'Rétablir',
      'Duplicate element': 'Dupliquer l’élément', 'Delete element': 'Supprimer l’élément',
      'Center horizontally': 'Centrer horizontalement', 'Center vertically': 'Centrer verticalement',
      'Insert icon…': 'Insérer une icône…', 'Font…': 'Police…',
      'Grid on label': 'Grille sur l’étiquette', 'Snap to grid': 'Aligner sur la grille',
      'Print regions': 'Zones d’impression', 'Detect roll format': 'Détecter le format du rouleau',
      'Refresh printer state': 'Actualiser l’état de l’imprimante', 'Paper & roll…': 'Papier et rouleau…',
      'Print from Windows…': 'Impression Windows…', 'Import NIIMBOT catalogue': 'Importer le catalogue NIIMBOT',
      'Statistics…': 'Statistiques…', 'About…': 'À propos…', 'Documentation (README)': 'Documentation (README)',
      Printer: 'Imprimante', 'Paper & roll': 'Papier et rouleau', 'Windows print': 'Impression Windows',
      'Format catalogue': 'Catalogue de formats', Statistics: 'Statistiques', About: 'À propos',
      Connection: 'Connexion', Scan: 'Rechercher', Refresh: 'Actualiser', Disconnect: 'Déconnecter',
      Model: 'Modèle', Service: 'Service', 'Paper calibration': 'Calibrage du papier',
      Cancel: 'Annuler', Save: 'Enregistrer', 'Save as copy': 'Enregistrer comme copie',
      'Save template': 'Enregistrer le modèle', 'Label name': 'Nom de l’étiquette', 'Template name': 'Nom du modèle',
      'Show all sizes': 'Afficher toutes les tailles', 'Icons & cliparts': 'Icônes et cliparts',
      Fonts: 'Polices', All: 'Tout', 'Search fonts…': 'Rechercher une police…',
      'Search icons (e.g. coffee, arrow, user…)': 'Rechercher une icône (ex. coffee, arrow, user…)',
      'No layers yet': 'Aucun calque', 'Roll size saved': 'Taille du rouleau enregistrée',
      Barcode: 'Code-barres', 'Ready-made size': 'Taille prédéfinie', 'Blank canvas': 'Zone vide',
      'fits roll': 'compatible', 'no data yet': 'aucune donnée',
    },
    es: {
      'Printer queue': 'Cola de impresi?n',
      Ready: 'Listo', 'Not Ready': 'No disponible', 'Checking?': 'Comprobando?',
      File: 'Archivo', Edit: 'Editar', View: 'Ver', Settings: 'Ajustes', Help: 'Ayuda',
      Language: 'Idioma', 'not connected': 'sin conexión', 'Connection state': 'Estado de conexión',
      Design: 'Diseño', Sheet: 'Hoja', 'Work area view': 'Área de trabajo',
      Properties: 'Propiedades', Layers: 'Capas',
      'Select an element on the label to edit it.': 'Selecciona un elemento de la etiqueta para editarlo.',
      Row: 'Fila', Column: 'Columna', Import: 'Importar', Clear: 'Limpiar',
      'Print sheet': 'Imprimir hoja', Mirror: 'Espejo', flip: 'voltear',
      'Sort…': 'Ordenar…', 'Sort direction': 'Dirección de orden', 'Sort rows by column': 'Ordenar filas por columna',
      'Name (mm)': 'Nombre (mm)', Font: 'Fuente', 'Size (pt)': 'Tamaño (pt)', 'Line height': 'Interlineado',
      Align: 'Alineación', Vertical: 'Vertical', Bold: 'Negrita', Italic: 'Cursiva', 'Auto fit': 'Ajustar',
      Rotation: 'Rotación', Text: 'Texto', Data: 'Datos', Format: 'Formato',
      'Text under code': 'Texto bajo el código', 'Error correction': 'Corrección de errores',
      'Line width (mm)': 'Grosor de línea (mm)', Fill: 'Rellenar', 'Thickness (mm)': 'Grosor (mm)',
      Image: 'Imagen', 'Pick image': 'Elegir imagen', 'Change image': 'Cambiar imagen',
      Print: 'Imprimir', 'Test print': 'Impresión de prueba', 'Save label': 'Guardar etiqueta',
      Copies: 'Copias', 'Print the sheet': 'Imprimir la hoja',
      Orientation: 'Orientación', 'Label orientation': 'Orientación de la etiqueta',
      'Rotate design 90° right': 'Girar el diseño 90° a la derecha',
      'Rotate design 90° left': 'Girar el diseño 90° a la izquierda',
      'Swap the label only (keep the design)': 'Solo cambiar las medidas',
      Density: 'Densidad', faintest: 'muy claro', lighter: 'más claro', standard: 'estándar',
      darker: 'más oscuro', darkest: 'muy oscuro', 'Dither photo': 'Tramar la foto',
      'Save label as…': 'Guardar etiqueta como…', 'Save as template…': 'Guardar como plantilla…',
      'New label': 'Nueva etiqueta', 'Load label…': 'Cargar etiqueta…', 'Export PNG': 'Exportar PNG',
      'Close window': 'Cerrar ventana', Undo: 'Deshacer', Redo: 'Rehacer',
      'Duplicate element': 'Duplicar elemento', 'Delete element': 'Eliminar elemento',
      'Center horizontally': 'Centrar horizontalmente', 'Center vertically': 'Centrar verticalmente',
      'Insert icon…': 'Insertar icono…', 'Font…': 'Fuente…',
      'Grid on label': 'Cuadrícula en la etiqueta', 'Snap to grid': 'Ajustar a la cuadrícula',
      'Print regions': 'Regiones de impresión', 'Detect roll format': 'Detectar formato del rollo',
      'Refresh printer state': 'Actualizar estado de la impresora', 'Paper & roll…': 'Papel y rollo…',
      'Print from Windows…': 'Impresión desde Windows…', 'Import NIIMBOT catalogue': 'Importar catálogo NIIMBOT',
      'Statistics…': 'Estadísticas…', 'About…': 'Acerca de…', 'Documentation (README)': 'Documentación (README)',
      Printer: 'Impresora', 'Paper & roll': 'Papel y rollo', 'Windows print': 'Impresión Windows',
      'Format catalogue': 'Catálogo de formatos', Statistics: 'Estadísticas', About: 'Acerca de',
      Connection: 'Conexión', Scan: 'Buscar', Refresh: 'Actualizar', Disconnect: 'Desconectar',
      Model: 'Modelo', Service: 'Servicio', 'Paper calibration': 'Calibración del papel',
      Cancel: 'Cancelar', Save: 'Guardar', 'Save as copy': 'Guardar como copia',
      'Save template': 'Guardar plantilla', 'Label name': 'Nombre de la etiqueta', 'Template name': 'Nombre de la plantilla',
      'Show all sizes': 'Mostrar todos los tamaños', 'Icons & cliparts': 'Iconos y cliparts',
      Fonts: 'Fuentes', All: 'Todos', 'Search fonts…': 'Buscar fuente…',
      'Search icons (e.g. coffee, arrow, user…)': 'Buscar icono (p. ej. coffee, arrow, user…)',
      'No layers yet': 'Aún no hay capas', 'Roll size saved': 'Tamaño del rollo guardado',
      Barcode: 'Código de barras', 'Ready-made size': 'Tamaño predefinido', 'Blank canvas': 'Lienzo vacío',
      'fits roll': 'cabe en el rollo', 'no data yet': 'aún sin datos',
    },
    it: {
      'Printer queue': 'Coda di stampa',
      Ready: 'Pronto', 'Not Ready': 'Non pronto', 'Checking?': 'Verifica?',
      File: 'File', Edit: 'Modifica', View: 'Vista', Settings: 'Impostazioni', Help: 'Aiuto',
      Language: 'Lingua', 'not connected': 'non connesso', 'Connection state': 'Stato connessione',
      Design: 'Progetto', Sheet: 'Foglio', 'Work area view': 'Area di lavoro',
      Properties: 'Proprietà', Layers: 'Livelli',
      'Select an element on the label to edit it.': 'Seleziona un elemento sull’etichetta per modificarlo.',
      Row: 'Riga', Column: 'Colonna', Import: 'Importa', Clear: 'Svuota',
      'Print sheet': 'Stampa foglio', Mirror: 'Specchio', flip: 'riflesso',
      'Sort…': 'Ordina…', 'Sort direction': 'Direzione ordinamento', 'Sort rows by column': 'Ordina righe per colonna',
      'Name (mm)': 'Nome (mm)', Font: 'Carattere', 'Size (pt)': 'Dimensione (pt)', 'Line height': 'Interlinea',
      Align: 'Allineamento', Vertical: 'Verticale', Bold: 'Grassetto', Italic: 'Corsivo', 'Auto fit': 'Adatta',
      Rotation: 'Rotazione', Text: 'Testo', Data: 'Dati', Format: 'Formato',
      'Text under code': 'Testo sotto il codice', 'Error correction': 'Correzione errori',
      'Line width (mm)': 'Spessore linea (mm)', Fill: 'Riempi', 'Thickness (mm)': 'Spessore (mm)',
      Image: 'Immagine', 'Pick image': 'Scegli immagine', 'Change image': 'Cambia immagine',
      Print: 'Stampa', 'Test print': 'Stampa di prova', 'Save label': 'Salva etichetta',
      Copies: 'Copie', 'Print the sheet': 'Stampa il foglio',
      Orientation: 'Orientamento', 'Label orientation': 'Orientamento etichetta',
      'Rotate design 90° right': 'Ruota il progetto di 90° a destra',
      'Rotate design 90° left': 'Ruota il progetto di 90° a sinistra',
      'Swap the label only (keep the design)': 'Scambia solo le misure',
      Density: 'Densità', faintest: 'molto chiaro', lighter: 'più chiaro', standard: 'standard',
      darker: 'più scuro', darkest: 'molto scuro', 'Dither photo': 'Retino la foto',
      'Save label as…': 'Salva etichetta come…', 'Save as template…': 'Salva come modello…',
      'New label': 'Nuova etichetta', 'Load label…': 'Carica etichetta…', 'Export PNG': 'Esporta PNG',
      'Close window': 'Chiudi finestra', Undo: 'Annulla', Redo: 'Ripeti',
      'Duplicate element': 'Duplica elemento', 'Delete element': 'Elimina elemento',
      'Center horizontally': 'Centra orizzontalmente', 'Center vertically': 'Centra verticalmente',
      'Insert icon…': 'Inserisci icona…', 'Font…': 'Carattere…',
      'Grid on label': 'Griglia sull’etichetta', 'Snap to grid': 'Aggancia alla griglia',
      'Print regions': 'Aree di stampa', 'Detect roll format': 'Rileva formato rotolo',
      'Refresh printer state': 'Aggiorna stato stampante', 'Paper & roll…': 'Carta e rotolo…',
      'Print from Windows…': 'Stampa da Windows…', 'Import NIIMBOT catalogue': 'Importa catalogo NIIMBOT',
      'Statistics…': 'Statistiche…', 'About…': 'Informazioni…', 'Documentation (README)': 'Documentazione (README)',
      Printer: 'Stampante', 'Paper & roll': 'Carta e rotolo', 'Windows print': 'Stampa Windows',
      'Format catalogue': 'Catalogo formati', Statistics: 'Statistiche', About: 'Informazioni',
      Connection: 'Connessione', Scan: 'Cerca', Refresh: 'Aggiorna', Disconnect: 'Disconnetti',
      Model: 'Modello', Service: 'Assistenza', 'Paper calibration': 'Calibrazione carta',
      Cancel: 'Annulla', Save: 'Salva', 'Save as copy': 'Salva come copia',
      'Save template': 'Salva modello', 'Label name': 'Nome etichetta', 'Template name': 'Nome modello',
      'Show all sizes': 'Mostra tutte le dimensioni', 'Icons & cliparts': 'Icone e clipart',
      Fonts: 'Font', All: 'Tutti', 'Search fonts…': 'Cerca font…',
      'Search icons (e.g. coffee, arrow, user…)': 'Cerca icona (es. coffee, arrow, user…)',
      'No layers yet': 'Ancora nessun livello', 'Roll size saved': 'Dimensione rotolo salvata',
      Barcode: 'Codice a barre', 'Ready-made size': 'Misura predefinita', 'Blank canvas': 'Area vuota',
      'fits roll': 'adatta al rotolo', 'no data yet': 'ancora nessun dato',
    },
  };

  const ATTRS = ['title', 'placeholder', 'aria-label'];
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT']);

  // The chosen language is stored by app.js in the SQLite ui_state table (nothing in the browser).
  let lang = 'en';

  const originals = new WeakMap();
  const attrOriginals = new WeakMap();
  const lastTranslations = new WeakMap();
  let translating = false;

  function t(text) {
    if (!text || lang === 'en') return text;
    const dict = DICT[lang];
    if (!dict) return text;
    const key = String(text).trim();
    if (!key) return text;
    const hit = dict[key];
    return hit ? String(text).replace(key, hit) : text;
  }

  function translateTextNode(node) {
    const stored = originals.get(node);
    if (stored === undefined) {
      originals.set(node, node.nodeValue);
    } else if (node.nodeValue !== stored && node.nodeValue !== lastTranslations.get(node)) {
      // the application wrote a new value into this node
      originals.set(node, node.nodeValue);
    }
    const next = t(originals.get(node));
    if (node.nodeValue !== next) node.nodeValue = next;
    lastTranslations.set(node, next);
  }

  function translateAttributes(element) {
    if (!element.getAttribute) return;
    const stored = attrOriginals.get(element) || {};
    let changed = false;
    for (const attr of ATTRS) {
      if (!element.hasAttribute(attr)) continue;
      const current = element.getAttribute(attr);
      const key = `last:${attr}`;
      if (!(attr in stored)) {
        stored[attr] = current;
        changed = true;
      } else if (current !== stored[attr] && current !== stored[key]) {
        stored[attr] = current;
      }
      const next = t(stored[attr]);
      if (current !== next) element.setAttribute(attr, next);
      stored[key] = next;
    }
    if (changed) attrOriginals.set(element, stored);
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (root.parentElement && !SKIP.has(root.parentElement.tagName)) translateTextNode(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    if (SKIP.has(root.tagName)) return;
    translateAttributes(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let node = walker.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.parentElement && !SKIP.has(node.parentElement.tagName)) translateTextNode(node);
      } else {
        translateAttributes(node);
      }
      node = walker.nextNode();
    }
  }

  function applyAll() {
    translating = true;
    try {
      walk(document.body);
      if (document.title) document.title = t(originals.get(document.title) || document.title) || document.title;
    } finally {
      translating = false;
    }
  }

  function setLanguage(code, options = {}) {
    if (!LOCALES.some((entry) => entry.code === code)) return;
    lang = code;
    document.documentElement.lang = code;
    applyAll();
    updateSwitcher();
    document.dispatchEvent(new CustomEvent('niibot:language-changed', {
      detail: { code, persist: options.persist !== false },
    }));
    if (options.silent !== true && window.lucide) {
      // toasts are generated by app.js; announce the change through a DOM event
      document.dispatchEvent(new CustomEvent('niibot:language', { detail: { code } }));
    }
  }

  /* ---------------------------------------------------------- language menu */
  let menuEl = null;

  function closeMenu() {
    if (menuEl) {
      menuEl.remove();
      menuEl = null;
    }
  }

  function openMenu() {
    closeMenu();
    const button = document.getElementById('lang-button');
    if (!button) return;
    const rect = button.getBoundingClientRect();
    menuEl = document.createElement('div');
    menuEl.className = 'lang-menu';
    menuEl.style.left = `${Math.max(8, rect.right - 190)}px`;
    menuEl.style.top = `${rect.bottom + 6}px`;
    menuEl.innerHTML = LOCALES.map((entry) => `
      <button data-lang="${entry.code}" class="${entry.code === lang ? 'active' : ''}">
        <span class="lang-flag">${entry.flag}</span>
        <span class="lang-label">${entry.label}</span>
        <span class="lang-code">${entry.code.toUpperCase()}</span>
      </button>`).join('');
    document.body.appendChild(menuEl);
    menuEl.querySelectorAll('[data-lang]').forEach((entry) => {
      entry.onclick = () => {
        setLanguage(entry.dataset.lang);
        closeMenu();
      };
    });
    setTimeout(() => {
      document.addEventListener('pointerdown', onOutside, { once: true });
    }, 0);
  }

  function onOutside(event) {
    if (menuEl && !menuEl.contains(event.target)) closeMenu();
  }

  function updateSwitcher() {
    const flag = document.getElementById('lang-flag');
    const entry = LOCALES.find((item) => item.code === lang) || LOCALES[0];
    if (flag) flag.textContent = entry.flag;
    const button = document.getElementById('lang-button');
    if (button) button.title = `Language: ${entry.label}`;
  }

  function mountSwitcher() {
    const button = document.getElementById('lang-button');
    if (button) {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        if (menuEl) closeMenu(); else openMenu();
      });
    }
    updateSwitcher();
    document.documentElement.lang = lang;
  }

  /* ------------------------------------------------------------ bootstrap */
  function start() {
    mountSwitcher();
    applyAll();
    const observer = new MutationObserver((mutations) => {
      if (translating || lang === 'en') return;
      translating = true;
      try {
        for (const mutation of mutations) {
          if (mutation.type === 'childList') {
            mutation.addedNodes.forEach((node) => walk(node));
          } else if (mutation.type === 'characterData') {
            walk(mutation.target);
          } else if (mutation.type === 'attributes' && mutation.target) {
            translateAttributes(mutation.target);
          }
        }
      } finally {
        translating = false;
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ATTRS,
    });
  }

  window.I18N = { locales: LOCALES, t, setLanguage, apply: applyAll, get language() { return lang; } };
  window.t = t;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
