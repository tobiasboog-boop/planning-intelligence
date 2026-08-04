"""
Notifica SDK — Client
=====================
Dunne HTTP wrapper rond de Notifica Data API.

Gebruik:
    from notifica_sdk import NotificaClient

    client = NotificaClient()  # leest uit .env
    df = client.query(1210, "SELECT * FROM ods.werkbonnen LIMIT 10")
"""

import os
import io
import pandas as pd
import requests

from .exceptions import (
    NotificaError, AuthError, PermissionError, ValidationError,
    TimeoutError, RateLimitError, ServerError,
)

# Probeer python-dotenv te laden (optioneel)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class NotificaClient:
    """Client voor de Notifica Data API.

    Configuratie via environment variabelen:
        NOTIFICA_API_URL  - Base URL (default: https://app.notifica.nl)
        NOTIFICA_APP_KEY  - API key uit App Beheer
        NOTIFICA_DATA_KEY - DWH data key (optioneel, voor directe DWH toegang)
    """

    def __init__(self, api_url: str = None, app_key: str = None, data_key: str = None):
        self.api_url = (api_url or os.getenv('NOTIFICA_API_URL', 'https://app.notifica.nl')).rstrip('/')
        self.app_key = app_key or os.getenv('NOTIFICA_APP_KEY', '')
        self.data_key = data_key or os.getenv('NOTIFICA_DATA_KEY', '')
        if not self.app_key and not self.data_key:
            raise AuthError("NOTIFICA_APP_KEY of NOTIFICA_DATA_KEY niet gevonden. Zet deze in .env of geef app_key=/data_key= mee.")
        self._session = requests.Session()
        headers = {'Content-Type': 'application/json'}
        # Data Key heeft voorrang — beide tegelijk sturen veroorzaakt auth-errors
        if self.data_key:
            headers['X-Data-Key'] = self.data_key
        elif self.app_key:
            headers['X-App-Key'] = self.app_key
        self._session.headers.update(headers)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Voer een HTTP request uit en vertaal fouten naar duidelijke exceptions."""
        url = f"{self.api_url}{path}"
        try:
            resp = self._session.request(method, url, timeout=120, **kwargs)
        except requests.ConnectionError:
            raise ServerError(f"Kan niet verbinden met {self.api_url}. Is de API bereikbaar?")
        except requests.Timeout:
            raise TimeoutError("Request timeout bij verbinden met de API.")

        if resp.status_code == 200:
            content_type = resp.headers.get('content-type', '')
            if 'application/json' in content_type:
                return resp.json()
            return {'raw': resp.text}

        # Fout responses vertalen
        try:
            error_data = resp.json()
            error_msg = error_data.get('error', resp.text)
        except Exception:
            error_msg = resp.text

        if resp.status_code == 401:
            raise AuthError(f"Ongeldige API key. Controleer NOTIFICA_APP_KEY.", status_code=401, detail=error_msg)
        elif resp.status_code == 403:
            raise PermissionError(f"Geen toegang: {error_msg}", status_code=403, detail=error_msg)
        elif resp.status_code == 400:
            raise ValidationError(f"Ongeldige request: {error_msg}", status_code=400, detail=error_msg)
        elif resp.status_code == 408:
            raise TimeoutError(f"Query timeout: {error_msg}", status_code=408, detail=error_msg)
        elif resp.status_code == 429:
            raise RateLimitError("Te veel requests. Max 60 per minuut.", status_code=429, detail=error_msg)
        else:
            raise ServerError(f"API fout ({resp.status_code}): {error_msg}", status_code=resp.status_code, detail=error_msg)

    def _raw_request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Voer een HTTP request uit en retourneer het raw Response object."""
        url = f"{self.api_url}{path}"
        return self._session.request(method, url, timeout=120, **kwargs)

    # ===== INFO =====

    def info(self) -> dict:
        """Haal app info op: beschikbare klanten en templates.

        Returns:
            dict met 'app', 'klanten', 'templates'
        """
        return self._request('GET', '/api/data/info')

    # ===== QUERIES =====

    def query(self, klantnummer: int, sql: str, max_rows: int = None) -> pd.DataFrame:
        """Voer een vrije SQL query uit (vereist dev_mode).

        Args:
            klantnummer: Klantnummer (bijv. 1210)
            sql: SELECT query
            max_rows: Max aantal rijen (optioneel, default uit app config)

        Returns:
            pandas DataFrame met resultaten
        """
        body = {'klantnummer': klantnummer, 'sql': sql}
        if max_rows:
            body['max_rows'] = max_rows
        result = self._request('POST', '/api/data/query', json=body)
        df = pd.DataFrame(result.get('rows', []), columns=result.get('columns', []))
        # API geeft numerieke waarden als strings terug — converteer waar mogelijk
        for col in df.columns:
            try:
                converted = pd.to_numeric(df[col], errors='coerce')
                if not converted.isna().all():
                    # Alleen overnemen als er daadwerkelijk numerieke waarden zijn
                    # en we niet per ongeluk tekst-kolommen wissen
                    orig_na = df[col].isna().sum()
                    if converted.isna().sum() <= orig_na:
                        df[col] = converted
            except (ValueError, TypeError):
                pass
        return df

    def query_template(self, klantnummer: int, template_name: str, parameters: dict = None) -> pd.DataFrame:
        """Voer een template-query uit.

        Args:
            klantnummer: Klantnummer (bijv. 1210)
            template_name: Naam van de geregistreerde template
            parameters: Dict met template parameters

        Returns:
            pandas DataFrame met resultaten
        """
        body = {'klantnummer': klantnummer}
        if parameters:
            body['parameters'] = parameters
        result = self._request('POST', f'/api/data/query/{template_name}', json=body)
        return pd.DataFrame(result.get('rows', []), columns=result.get('columns', []))

    # ===== SCHEMA =====

    def schema(self, klantnummer: int) -> dict:
        """Ontdek het database schema (vereist dev_mode).

        Args:
            klantnummer: Klantnummer

        Returns:
            dict met schemas en tabellen
        """
        return self._request('GET', f'/api/data/schema/{klantnummer}')

    # ===== WRITE =====

    def write(self, klantnummer: int, sql: str) -> dict:
        """Schrijf naar een app_* schema (vereist dev_mode voor vrije SQL).

        Args:
            klantnummer: Klantnummer
            sql: INSERT/UPDATE/DELETE query (alleen app_* schemas)

        Returns:
            dict met resultaat
        """
        return self._request('POST', '/api/data/write', json={
            'klantnummer': klantnummer,
            'sql': sql,
        })

    def write_template(self, klantnummer: int, template_name: str, parameters: dict = None) -> dict:
        """Schrijf via een template.

        Args:
            klantnummer: Klantnummer
            template_name: Naam van de write template
            parameters: Dict met template parameters

        Returns:
            dict met resultaat
        """
        body = {'klantnummer': klantnummer}
        if parameters:
            body['parameters'] = parameters
        return self._request('POST', f'/api/data/write/{template_name}', json=body)

    # ===== CSV =====

    def csv_batches(self, klantnummer: int, days: int = None) -> list:
        """Lijst beschikbare CSV batches.

        Args:
            klantnummer: Klantnummer
            days: Aantal dagen terug (optioneel)

        Returns:
            Lijst met batch info dicts
        """
        params = {}
        if days:
            params['days'] = days
        result = self._request('GET', f'/api/data/csv/{klantnummer}/batches', params=params)
        return result.get('batches', result) if isinstance(result, dict) else result

    def csv_files(self, klantnummer: int, date: str, folder: str) -> list:
        """Lijst bestanden in een CSV batch.

        Args:
            klantnummer: Klantnummer
            date: Datum (YYYY-MM-DD)
            folder: Map naam

        Returns:
            Lijst met bestandsnamen
        """
        result = self._request('GET', f'/api/data/csv/{klantnummer}/{date}/{folder}/files')
        return result.get('files', result) if isinstance(result, dict) else result

    def csv_download(self, klantnummer: int, date: str, folder: str, filename: str) -> pd.DataFrame:
        """Download een CSV bestand als DataFrame.

        Args:
            klantnummer: Klantnummer
            date: Datum (YYYY-MM-DD)
            folder: Map naam
            filename: Bestandsnaam

        Returns:
            pandas DataFrame
        """
        resp = self._raw_request('GET', f'/api/data/csv/{klantnummer}/{date}/{folder}/{filename}')
        if resp.status_code != 200:
            try:
                error_msg = resp.json().get('error', resp.text)
            except Exception:
                error_msg = resp.text
            raise ServerError(f"CSV download mislukt: {error_msg}", status_code=resp.status_code)
        return pd.read_csv(io.StringIO(resp.text))

    # ===== TEMPLATES =====

    def templates(self) -> list:
        """Lijst alle templates voor deze app.

        Returns:
            Lijst met template dicts
        """
        result = self._request('GET', '/api/data/templates')
        return result.get('templates', result) if isinstance(result, dict) else result

    def register_template(self, name: str, sql: str, parameters: list = None,
                          description: str = None, target_type: str = 'dwh') -> dict:
        """Registreer een nieuwe query template.

        Args:
            name: Template naam (uniek per app)
            sql: SQL met :parameter placeholders
            parameters: Lijst met parameter definities
            description: Beschrijving
            target_type: 'dwh' of 'csv'

        Returns:
            dict met template info
        """
        body = {
            'name': name,
            'sql_template': sql,
            'target_type': target_type,
        }
        if parameters:
            body['parameters'] = parameters
        if description:
            body['description'] = description
        return self._request('POST', '/api/data/templates/register', json=body)
