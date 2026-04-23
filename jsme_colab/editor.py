import uuid
from IPython import get_ipython
from IPython.display import display, HTML

JSME_JS_URL = "https://jsme-editor.github.io/dist/jsme/jsme.nocache.js"

_LOAD_JS = """
<script>
(function() {
    if (window._jsme_colab_loaded) return;
    window._jsme_colab_loaded = true;
    window._jsme_instances = {};
    window._jsme_queue = [];

    window.jsmeOnLoad = function() {
        window._jsme_ready = true;
        window._jsme_queue.forEach(function(fn) { fn(); });
        window._jsme_queue = [];
    };

    var s = document.createElement('script');
    s.src = '__JSME_URL__';
    document.head.appendChild(s);
})();
</script>
""".replace("__JSME_URL__", JSME_JS_URL)

# __IID__, __SMILES__, __WIDTH__, __HEIGHT__, __OPTIONS__ are replaced before use.
_INSTANCE_JS = """
<input  type="hidden" id="jsme_smiles___IID__" value="__SMILES__">
<div    id="jsme_container___IID__"></div>
<div    id="jsme_display___IID__"
        style="font:12px/1.4 monospace;color:#555;margin-top:4px;min-height:1em">__SMILES__</div>
<script>
(function() {
    var iid        = '__IID__';
    var initSmiles = '__SMILES__';
    var commTarget = 'jsme___IID__';

    /* Update the hidden input and visible label. */
    function onSmilesChange(smiles) {
        var inp  = document.getElementById('jsme_smiles_' + iid);
        var disp = document.getElementById('jsme_display_' + iid);
        if (inp)  inp.value     = smiles;
        if (disp) disp.textContent = smiles;
    }

    function initEditor() {
        var applet = new JSApplet.JSME(
            'jsme_container_' + iid,
            '__WIDTH__', '__HEIGHT__',
            {options: '__OPTIONS__'}
        );
        if (initSmiles) applet.readGenericMolecularInput(initSmiles);
        window._jsme_instances[iid] = applet;

        /* Open a Jupyter comm so Python receives every SMILES change.
           Works in classic Jupyter Notebook; silently skipped elsewhere. */
        var comm = null;
        try {
            var nb = (window.Jupyter  && Jupyter.notebook)
                  || (window.IPython  && IPython.notebook);
            if (nb && nb.kernel && nb.kernel.comm_manager) {
                comm = nb.kernel.comm_manager.new_comm(commTarget, {});
            }
        } catch(e) {}

        function handleChange() {
            var smiles = applet.smiles();
            onSmilesChange(smiles);
            if (comm) {
                try { comm.send({smiles: smiles}); } catch(e) {}
            }
        }

        /* Primary: JSME callback (instant). */
        applet.setCallBack('AfterStructureModified', handleChange);

        /* Fallback: poll every 300 ms in case setCallBack does not fire
           (observed in some Colab environments). */
        var _lastSmiles = initSmiles;
        setInterval(function() {
            try {
                var s = applet.smiles();
                if (s !== _lastSmiles) { _lastSmiles = s; handleChange(); }
            } catch(e) {}
        }, 300);
    }

    if (window._jsme_ready) {
        initEditor();
    } else {
        window._jsme_queue = window._jsme_queue || [];
        window._jsme_queue.push(initEditor);
    }
})();
</script>
"""


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
        self._id = uuid.uuid4().hex[:8]
        self._width   = f"{width}px"  if isinstance(width,  int) else width
        self._height  = f"{height}px" if isinstance(height, int) else height
        self._options = options

        if mol is not None:
            smiles = _mol_to_smiles(mol)
        self._initial_smiles = smiles or ""
        self._smiles = self._initial_smiles   # kept current by _handle_comm

        # Register a comm target so the JS side can push SMILES to Python.
        try:
            ip = get_ipython()
            if ip and getattr(ip, 'kernel', None) is not None:
                ip.kernel.comm_manager.register_target(
                    f'jsme_{self._id}', self._handle_comm
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Comm handler (Jupyter only)
    # ------------------------------------------------------------------

    def _handle_comm(self, comm, open_msg):
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
        return _LOAD_JS + self._build_instance_html()

    def _build_instance_html(self) -> str:
        safe = (
            self._initial_smiles
            .replace("\\", "\\\\")
            .replace("'",  "\\'")
            .replace('"',  '\\"')
        )
        return (
            _INSTANCE_JS
            .replace("__IID__",     self._id)
            .replace("__SMILES__",  safe)
            .replace("__WIDTH__",   self._width)
            .replace("__HEIGHT__",  self._height)
            .replace("__OPTIONS__", self._options)
        )

    # ------------------------------------------------------------------
    # SMILES / Mol access
    # ------------------------------------------------------------------

    def get_smiles(self) -> str:
        """Return the current SMILES.

        * **Colab** — synchronous read via ``eval_js``.
        * **Jupyter Notebook** — returns the value last pushed by the
          ``AfterStructureModified`` callback.  Run this from a *separate
          cell* so the kernel has processed the JS message.
        """
        try:
            from google.colab.output import eval_js
            result = eval_js(
                f"(document.getElementById('jsme_smiles_{self._id}')||{{}}).value||''"
            )
            return result or ""
        except ImportError:
            pass
        return self._smiles

    def set_smiles(self, smiles: str):
        """Update the molecule in an already-displayed editor."""
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

    Returns the editor so you can later call :meth:`~JSMEEditor.get_smiles`
    or :meth:`~JSMEEditor.get_mol`.

    Example::

        editor = embed("CC(=O)Oc1ccccc1C(=O)O")
        # draw / edit the molecule …
        smiles = editor.get_smiles()
    """
    editor = JSMEEditor(smiles=smiles, mol=mol, **kwargs)
    editor.show()
    return editor
