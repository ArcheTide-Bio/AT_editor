import uuid
from IPython import get_ipython
from IPython.display import display, HTML

JSME_JS_URL = "https://unpkg.com/jsme-editor/jsme.nocache.js"

# Module-level registry so kernel.execute() code can reach editor instances.
_registry: dict = {}


def _set(editor_id: str, smiles: str) -> None:
    """Called by kernel.execute() in Jupyter to push a SMILES update."""
    ed = _registry.get(editor_id)
    if ed is not None:
        ed._smiles = smiles


# Per-instance HTML — __IID__, __SMILES__, __WIDTH__, __HEIGHT__, __OPTIONS__
# are replaced in _build_instance_html().
# The <script src> tag loads JSME directly (works in Colab's CSP where
# document.head.appendChild is blocked).  jsmeOnLoad is set up before the
# tag so GWT finds it on first load; subsequent loads see JSApplet already
# defined and skip the queue entirely.
_INSTANCE_JS = """
<script type="text/javascript">
if (!window._jsme_instances) {
    window._jsme_instances = {};
    window._jsme_queue     = [];
    window.jsmeOnLoad = function() {
        window._jsme_ready = true;
        window._jsme_queue.forEach(function(fn) { fn(); });
        window._jsme_queue = [];
    };
}
</script>
<script type="text/javascript" language="javascript"
        src="__JSME_URL__"></script>
<input type="hidden" id="jsme_smiles___IID__" value="__SMILES__">
<div   id="jsme_status___IID__"
       style="font:12px sans-serif;color:#888;padding:4px">Loading JSME editor…</div>
<div   id="jsme_container___IID__"></div>
<div   id="jsme_display___IID__"
       style="font:12px/1.4 monospace;color:#555;margin-top:4px;min-height:1em">__SMILES__</div>
<script type="text/javascript">
(function() {
    var iid        = '__IID__';
    var initSmiles = '__SMILES__';

    function onSmilesChange(smiles) {
        var inp  = document.getElementById('jsme_smiles_' + iid);
        var disp = document.getElementById('jsme_display_' + iid);
        if (inp)  inp.value        = smiles;
        if (disp) disp.textContent = smiles;

        if (window.google !== undefined) {
            // Colab (hosted runtime)
            google.colab.kernel.invokeFunction('jsme_cb___IID__', [smiles], {});
            return;
        }
        // Classic Jupyter Notebook / JupyterLab
        var nb = (window.Jupyter  && Jupyter.notebook)
              || (window.IPython  && IPython.notebook);
        if (nb && nb.kernel) {
            var safe = smiles.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
            nb.kernel.execute(
                'import jsme_colab as _j; _j._set("' + iid + '",\'' + safe + '\')'
            );
            return;
        }
        // JupyterLab via window.jupyterapp
        try {
            var panel  = window.jupyterapp && window.jupyterapp.shell.currentWidget;
            var kernel = panel && panel.sessionContext
                      && panel.sessionContext.session
                      && panel.sessionContext.session.kernel;
            if (kernel && window._jsme_lab_comm && window._jsme_lab_comm[iid]) {
                window._jsme_lab_comm[iid].send({smiles: smiles});
            }
        } catch(e) {}
    }

    function initEditor() {
        var status = document.getElementById('jsme_status_' + iid);
        if (status) status.style.display = 'none';
        var applet = new JSApplet.JSME(
            'jsme_container_' + iid,
            '__WIDTH__', '__HEIGHT__',
            {options: '__OPTIONS__'}
        );
        if (initSmiles) applet.readGenericMolecularInput(initSmiles);
        window._jsme_instances[iid] = applet;

        // JupyterLab: open one comm per editor for reuse
        try {
            var panel  = window.jupyterapp && window.jupyterapp.shell.currentWidget;
            var kernel = panel && panel.sessionContext
                      && panel.sessionContext.session
                      && panel.sessionContext.session.kernel;
            if (kernel) {
                window._jsme_lab_comm = window._jsme_lab_comm || {};
                var comm = kernel.createComm('jsme___IID__');
                comm.open({});
                window._jsme_lab_comm[iid] = comm;
            }
        } catch(e) {}

        applet.setCallBack('AfterStructureModified', function() {
            onSmilesChange(applet.smiles());
        });
    }

    // If JSApplet is already available (JSME cached), init immediately.
    if (typeof JSApplet !== 'undefined' && JSApplet.JSME) {
        initEditor();
    } else if (window._jsme_ready) {
        initEditor();
    } else {
        window._jsme_queue = window._jsme_queue || [];
        window._jsme_queue.push(initEditor);
        // Show error if JSME hasn't loaded after 10 s (CSP / network block).
        setTimeout(function() {
            if (!window._jsme_instances || !window._jsme_instances[iid]) {
                var s = document.getElementById('jsme_status_' + iid);
                if (s) {
                    s.style.color = '#c00';
                    s.textContent = 'JSME failed to load. CDN may be blocked. URL: __JSME_URL__';
                    s.style.display = '';
                }
            }
        }, 10000);
    }
})();
</script>
""".replace("__JSME_URL__", JSME_JS_URL)


def _mol_to_smiles(mol) -> str:
    from rdkit import Chem
    return Chem.MolToSmiles(mol)


class JSMEEditor:
    """Embed the JSME molecular editor in a Jupyter / Colab notebook.

    Parameters
    ----------
    smiles : str, optional
        SMILES string to pre-populate the editor.
    mol : rdkit.Chem.Mol, optional
        RDKit molecule to pre-populate (converted to SMILES).
    width, height : str or int
        Editor dimensions — integers are treated as pixels.
    options : str
        Comma-separated JSME option flags (e.g. ``"query,hydrogens"``).
    """

    def __init__(self, smiles: str = "", mol=None,
                 width="380px", height="340px", options: str = ""):
        self._id     = uuid.uuid4().hex[:8]
        self._width  = f"{width}px"  if isinstance(width,  int) else width
        self._height = f"{height}px" if isinstance(height, int) else height
        self._options = options

        if mol is not None:
            smiles = _mol_to_smiles(mol)
        self._initial_smiles = smiles or ""
        self._smiles = self._initial_smiles

        # Keep a reference so kernel.execute() code can find this instance.
        _registry[self._id] = self

        # Colab: register the invokeFunction callback before showing the editor.
        try:
            from google.colab import output
            output.register_callback(
                f'jsme_cb_{self._id}',
                lambda s: setattr(self, '_smiles', s),
            )
        except ImportError:
            pass

        # JupyterLab: register a comm target so the JS kernel.createComm() call
        # can deliver SMILES updates via the standard Jupyter comm protocol.
        try:
            ip = get_ipython()
            if ip and getattr(ip, 'kernel', None) is not None:
                ip.kernel.comm_manager.register_target(
                    f'jsme_{self._id}', self._handle_comm
                )
        except Exception:
            pass

    def _handle_comm(self, comm, open_msg):
        """Receive SMILES pushed from JupyterLab via kernel comm."""
        @comm.on_msg
        def _(msg):
            self._smiles = msg['content']['data'].get('smiles', self._smiles)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def show(self):
        """Render the editor."""
        display(HTML(self._build_html()))

    def _repr_html_(self) -> str:
        return self._build_html()

    def _build_html(self) -> str:
        return self._build_instance_html()

    def _build_instance_html(self) -> str:
        safe = (
            self._initial_smiles
            .replace("\\", "\\\\")
            .replace("'",  "\\'")
            .replace('"',  '\\"')
        )
        html = (
            _INSTANCE_JS
            .replace("__IID__",     self._id)
            .replace("__SMILES__",  safe)
            .replace("__WIDTH__",   self._width)
            .replace("__HEIGHT__",  self._height)
            .replace("__OPTIONS__", self._options)
        )
        return html

    # ------------------------------------------------------------------
    # SMILES / Mol access
    # ------------------------------------------------------------------

    def get_smiles(self) -> str:
        """Return the current SMILES.

        * **Colab** — reads the hidden ``<input>`` that the JS callback keeps
          current on every edit, via ``eval_js`` (synchronous).
        * **Jupyter Notebook** — returns ``self._smiles`` updated by
          ``kernel.execute()`` on every edit; call from a *separate cell*.
        """
        try:
            from google.colab.output import eval_js
            result = eval_js(
                f"(document.getElementById('jsme_smiles_{self._id}')||{{}}).value"
            )
            if result is not None:
                return result
        except ImportError:
            pass
        return self._smiles

    def set_smiles(self, smiles: str):
        """Update the molecule in an already-displayed editor."""
        self._smiles = smiles
        safe = smiles.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        js = (
            f"(function(){{"
            f"var a=window._jsme_instances&&window._jsme_instances['{self._id}'];"
            f"if(a)a.readGenericMolecularInput('{safe}');"
            f"}})()"
        )
        try:
            from google.colab.output import eval_js
            eval_js(js)
            return
        except ImportError:
            pass
        from IPython.display import Javascript
        display(Javascript(js))

    def get_mol(self):
        """Return an RDKit ``Mol`` for the current editor content, or ``None``."""
        from rdkit import Chem
        smiles = self.get_smiles()
        if not smiles:
            return None
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"RDKit could not parse SMILES: {smiles!r}")
        return mol


# ------------------------------------------------------------------
# Convenience
# ------------------------------------------------------------------

def embed(smiles: str = "", mol=None, **kwargs) -> "JSMEEditor":
    """Create and immediately display a :class:`JSMEEditor`.

    Example::

        editor = embed("CC(=O)Oc1ccccc1C(=O)O")
        # draw / edit …
        smiles = editor.get_smiles()
    """
    editor = JSMEEditor(smiles=smiles, mol=mol, **kwargs)
    editor.show()
    return editor
