import uuid
from IPython.display import display, HTML

JSME_JS_URL = "https://jsme-editor.github.io/dist/jsme/jsme.nocache.js"

# JS snippet injected once per notebook to load JSME and manage instances.
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

# Per-instance init + SMILES-sync JS.
# __IID__, __SMILES__, __WIDTH__, __HEIGHT__, __OPTIONS__, __MODEL_ID__
# are replaced before injection.
_INSTANCE_JS = """
<input type="hidden" id="jsme_smiles___IID__" value="__SMILES__">
<div id="jsme_container___IID__"></div>
<script>
(function() {
    var iid       = '__IID__';
    var modelId   = '__MODEL_ID__';
    var initSmiles = '__SMILES__';

    function syncToWidget(smiles) {
        // Always update the hidden input — eval_js reads this in Colab.
        var inp = document.getElementById('jsme_smiles_' + iid);
        if (inp) inp.value = smiles;

        // ── Classic Jupyter: proper ipywidgets comm protocol ──────────────
        try {
            var comm = IPython.notebook.kernel.comm_manager.comms[modelId];
            if (comm) {
                comm.send({method: 'update', state: {value: smiles}, buffer_paths: []});
                return;
            }
        } catch(e) {}

        // ── JupyterLab / VS Code: requirejs ──────────────────────────────
        try {
            require(['@jupyter-widgets/base'], function(base) {
                var mgr = base.ManagerBase._managers && base.ManagerBase._managers[0];
                if (mgr) {
                    mgr.get_model(modelId).then(function(m) {
                        m.set('value', smiles);
                        m.save_changes();
                    });
                }
            });
        } catch(e) {}
    }

    function initEditor() {
        var applet = new JSApplet.JSME(
            'jsme_container_' + iid,
            '__WIDTH__', '__HEIGHT__',
            {options: '__OPTIONS__'}
        );
        if (initSmiles) applet.readGenericMolecularInput(initSmiles);
        window._jsme_instances[iid] = applet;

        applet.setCallBack('AfterStructureModified', function(ev) {
            syncToWidget(ev.src.smiles());
        });
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
    width, height : str
        Editor dimensions (CSS length strings, e.g. ``"380px"``).
    options : str
        Comma-separated JSME option flags (e.g. ``"query,hydrogens"``).
    """

    def __init__(self, smiles: str = "", mol=None,
                 width="380px", height="340px",
                 options: str = ""):
        try:
            import ipywidgets as widgets
        except ImportError as e:
            raise ImportError(
                "jsme_colab requires ipywidgets: pip install ipywidgets"
            ) from e

        self._id = uuid.uuid4().hex[:8]
        self._width = f"{width}px" if isinstance(width, int) else width
        self._height = f"{height}px" if isinstance(height, int) else height
        self._options = options

        if mol is not None:
            smiles = _mol_to_smiles(mol)
        self._initial_smiles = smiles or ""

        # Hidden Text widget — JS writes here, Python reads here.
        self._widget = widgets.Text(
            value=self._initial_smiles,
            layout=widgets.Layout(display="none"),
        )

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _repr_html_(self) -> str:
        return self._build_html()

    def show(self):
        """Render the editor (call when auto-display is not triggered)."""
        import ipywidgets as widgets
        display(widgets.VBox([self._widget]))  # display hidden widget first
        display(HTML(_LOAD_JS + self._build_instance_html()))

    def _build_html(self) -> str:
        import ipywidgets as widgets
        # _repr_html_ can't display widget objects, so we only return the
        # JSME HTML; IPython will call _repr_html_ and also display the widget
        # via the widget protocol automatically when using display().
        return _LOAD_JS + self._build_instance_html()

    def _build_instance_html(self) -> str:
        safe_smiles = (
            self._initial_smiles
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace('"', '\\"')
        )
        return (
            _INSTANCE_JS
            .replace("__IID__",      self._id)
            .replace("__MODEL_ID__", self._widget.model_id)
            .replace("__SMILES__",   safe_smiles)
            .replace("__WIDTH__",    self._width)
            .replace("__HEIGHT__",   self._height)
            .replace("__OPTIONS__",  self._options)
        )

    # ------------------------------------------------------------------
    # SMILES / Mol access
    # ------------------------------------------------------------------

    def get_smiles(self) -> str:
        """Return the current SMILES from the editor.

        * **Colab** — uses ``eval_js`` for a synchronous, up-to-the-moment read.
        * **Jupyter Notebook / Lab / VS Code** — reads the widget value that
          JavaScript keeps up to date via the ``AfterStructureModified``
          callback.  Call this from a *separate cell* after editing so the
          kernel has had time to process the async update.
        """
        # Colab: read the hidden input that JS keeps current on every edit.
        try:
            from google.colab.output import eval_js
            result = eval_js(
                f"(document.getElementById('jsme_smiles_{self._id}') || {{}}).value || ''"
            )
            return result or ""
        except ImportError:
            pass

        # Jupyter: widget value is pushed by JS via the ipywidgets comm.
        return self._widget.value

    def set_smiles(self, smiles: str):
        """Update the molecule shown in an already-displayed editor.

        Works in Colab (via ``eval_js``) and classic Jupyter Notebook
        (via ``kernel.execute``).  In JupyterLab / VS Code, call
        :meth:`show` again with the new SMILES instead.
        """
        safe = smiles.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        try:
            from google.colab.output import eval_js
            eval_js(
                f"(function(){{"
                f"  var a=window._jsme_instances['{self._id}'];"
                f"  if(a) a.readGenericMolecularInput('{safe}');"
                f"}})()"
            )
            return
        except (ImportError, Exception):
            pass

        # Classic Jupyter: run JS via display
        from IPython.display import Javascript
        display(Javascript(
            f"(function(){{"
            f"  var a=window._jsme_instances['{self._id}'];"
            f"  if(a) a.readGenericMolecularInput('{safe}');"
            f"}})()"
        ))

    def get_mol(self):
        """Return an RDKit ``Mol`` for the current editor content.

        Returns ``None`` if the canvas is empty.
        """
        from rdkit import Chem
        smiles = self.get_smiles()
        if not smiles:
            return None
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise ValueError(f"RDKit could not parse SMILES: {smiles!r}")
        return mol


# ------------------------------------------------------------------
# Convenience function
# ------------------------------------------------------------------

def embed(smiles: str = "", mol=None, **kwargs) -> "JSMEEditor":
    """Create and immediately display a :class:`JSMEEditor`.

    Returns the editor instance so you can later call
    :meth:`~JSMEEditor.get_smiles` or :meth:`~JSMEEditor.get_mol`.

    Example::

        editor = embed("CC(=O)Oc1ccccc1C(=O)O")
        # ... edit in the notebook ...
        smiles = editor.get_smiles()
    """
    editor = JSMEEditor(smiles=smiles, mol=mol, **kwargs)
    editor.show()
    return editor
