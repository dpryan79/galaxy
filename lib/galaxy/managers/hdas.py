"""
Manager and Serializer for HDAs.

HistoryDatasetAssociations (HDAs) are datasets contained or created in a
history.
"""

import os
import gettext

from galaxy import model
from galaxy import exceptions
from galaxy import datatypes
from galaxy import objectstore

from galaxy.managers import datasets
from galaxy.managers import secured
from galaxy.managers import taggable
from galaxy.managers import annotatable
from galaxy.managers import users

import logging
log = logging.getLogger( __name__ )


class HDAManager( datasets.DatasetAssociationManager,
                  secured.OwnableManagerMixin,
                  taggable.TaggableManagerMixin,
                  annotatable.AnnotatableManagerMixin ):
    """
    Interface/service object for interacting with HDAs.
    """
    model_class = model.HistoryDatasetAssociation
    foreign_key_name = 'history_dataset_association'

    tag_assoc = model.HistoryDatasetAssociationTagAssociation
    annotation_assoc = model.HistoryDatasetAssociationAnnotationAssociation

    # TODO: move what makes sense into DatasetManager
    # TODO: which of these are common with LDDAs and can be pushed down into DatasetAssociationManager?

    def __init__( self, app ):
        """
        Set up and initialize other managers needed by hdas.
        """
        super( HDAManager, self ).__init__( app )
        self.user_manager = users.UserManager( app )

    # .... security and permissions
    def is_accessible( self, trans, hda, user ):
        """
        Override to allow owners (those that own the associated history).
        """
        if self.is_owner( trans, hda, user ):
            return True
        return super( HDAManager, self ).is_accessible( trans, hda, user )

    def is_owner( self, trans, hda, user ):
        """
        Use history to see if current user owns HDA.
        """
        history = hda.history
        # TODO: some dup here with historyManager.is_owner but prevents circ import
        if self.user_manager.is_admin( trans, user ):
            return True
        if self.user_manager.is_anonymous( user ) and history == trans.get_history():
            return True
        return history.user == user

    # .... create and copy
    def create( self, trans, history=None, dataset=None, flush=True, **kwargs ):
        """
        Create a new hda optionally passing in it's history and dataset.

        ..note: to explicitly set hid to `None` you must pass in `hid=None`, otherwise
        it will be automatically set.
        """
        if not dataset:
            kwargs[ 'create_dataset' ] = True
        hda = super( HDAManager, self ).create( trans,
                                                flush=flush,
                                                history=history,
                                                dataset=dataset,
                                                sa_session=self.app.model.context, **kwargs )

        if history:
            # TODO Probably Bug:  set_hid is never used, and should be passed
            # to history.add_dataset here.
            set_hid = not ( 'hid' in kwargs )
            history.add_dataset( hda )
        #TODO:?? some internal sanity check here (or maybe in add_dataset) to make sure hids are not duped?

        self.session().add( hda )
        if flush:
            self.session().flush()
        return hda

    def copy( self, trans, hda, history=None, **kwargs ):
        """
        Copy and return the given HDA.
        """
        # TODO:?? not using the following as this fn does not set history and COPIES hid (this doesn't seem correct)
        # return hda.copy()
        copy = model.HistoryDatasetAssociation(
            name        = hda.name,
            info        = hda.info,
            blurb       = hda.blurb,
            peek        = hda.peek,
            tool_version= hda.tool_version,
            extension   = hda.extension,
            dbkey       = hda.dbkey,
            dataset     = hda.dataset,
            visible     = hda.visible,
            deleted     = hda.deleted,
            parent_id   = kwargs.get( 'parent_id', None ),
        )
        # add_dataset will update the hid to the next avail. in history
        if history:
            history.add_dataset( copy )

        copy.copied_from_history_dataset_association = hda
        copy.set_size()

        # TODO: update from kwargs?

        # Need to set after flushed, as MetadataFiles require dataset.id
        self.session().add( copy )
        self.session().flush()
        copy.metadata = hda.metadata

        # In some instances peek relies on dataset_id, i.e. gmaj.zip for viewing MAFs
        if not hda.datatype.copy_safe_peek:
            copy.set_peek()

        self.session().flush()
        return copy

    def copy_ldda( self, trans, history, ldda, **kwargs ):
        """
        Copy this HDA as a LDDA and return.
        """
        return ldda.to_history_dataset_association( history, add_to_history=True )

    # .... deletion and purging
    def purge( self, trans, hda, flush=True ):
        """
        Purge this HDA and the dataset underlying it.
        """
        super( HDAManager, self ).purge( trans, hda, flush=flush )
        # decreate the user's space used
        if trans.user:
            trans.user.total_disk_usage -= hda.quota_amount( trans.user )
        return hda

    # .... states
    def error_if_uploading( self, trans, hda ):
        """
        Raise error if HDA is still uploading.
        """
        #TODO: may be better added to an overridden get_accessible
        if hda.state == model.Dataset.states.UPLOAD:
            raise exceptions.Conflict( "Please wait until this dataset finishes uploading" )
        return hda

    def data_conversion_status( self, trans, hda ):
        """
        Returns a message if an hda is not ready to be used in visualization.
        """
        HDA_model = model.HistoryDatasetAssociation
        # this is a weird syntax and return val
        if not hda:
            return HDA_model.conversion_messages.NO_DATA
        if hda.state == model.Job.states.ERROR:
            return HDA_model.conversion_messages.ERROR
        if hda.state != model.Job.states.OK:
            return HDA_model.conversion_messages.PENDING
        return None

    # .... associated job

    # .... data
    # TODO: to data provider or Text datatype directly
    def text_data( self, hda, preview=True ):
        """
        Get data from text file, truncating if necessary.
        """
        # 1 MB
        MAX_PEEK_SIZE = 1000000

        truncated = False
        hda_data = None
        # For now, cannot get data from non-text datasets.
        if not isinstance( hda.datatype, datatypes.data.Text ):
            return truncated, hda_data
        if not os.path.exists( hda.file_name ):
            return truncated, hda_data

        truncated = preview and os.stat( hda.file_name ).st_size > MAX_PEEK_SIZE
        hda_data = open( hda.file_name ).read( max_peek_size )
        return truncated, hda_data


class HDASerializer( # datasets._UnflattenedMetadataDatasetAssociationSerializer,
                     datasets.DatasetAssociationSerializer,
                     taggable.TaggableSerializerMixin,
                     annotatable.AnnotatableSerializerMixin ):
    # TODO: inherit from datasets.DatasetAssociationSerializer
    # TODO: move what makes sense into DatasetSerializer

    def __init__( self, app ):
        super( HDASerializer, self ).__init__( app )
        self.hda_manager = HDAManager( app )

        self.default_view = 'summary'
        self.add_view( 'summary', [
            'id', 'name',
            'type_id',
            'history_id', 'hid',
            # why include if model_class is there?
            'history_content_type',
            'dataset_id',
            'state', 'extension',
            'deleted', 'purged', 'visible', 'resubmitted',
            'type', 'url'
        ])
        self.add_view( 'detailed', [
            'model_class',
            'history_id', 'hid',
            # why include if model_class is there?
            'hda_ldda',
            #TODO: accessible needs to go away
            'accessible',

            # remapped
            'genome_build', 'misc_info', 'misc_blurb',
            'file_ext', 'file_size',
            'file_path',

            'create_time', 'update_time',
            'metadata', 'meta_files', 'data_type',
            'peek',

            'uuid',
            'permissions',

            'display_apps',
            'display_types',
            'visualizations',

            #'url',
            'download_url',

            'annotation', 'tags',

            'api_type'
        ], include_keys_from='summary' )

        self.add_view( 'extended', [
            'tool_version', 'parent_id', 'designation',
        ], include_keys_from='detailed' )

        # keyset returned to create show a dataset where the owner has no access
        self.add_view( 'inaccessible', [
            'id', 'name', 'history_id', 'hid', 'history_content_type',
            'state', 'deleted', 'visible'
        ])

    def add_serializers( self ):
        super( HDASerializer, self ).add_serializers()
        taggable.TaggableSerializerMixin.add_serializers( self )
        annotatable.AnnotatableSerializerMixin.add_serializers( self )

        self.serializers.update({
            'model_class'   : lambda *a: 'HistoryDatasetAssociation',
            'history_content_type': lambda *a: 'dataset',
            'hda_ldda'      : lambda *a: 'hda',
            'type_id'       : self.serialize_type_id,

            'history_id'    : self.serialize_id,

            # remapped
            'misc_info'     : self._remap_from( 'info' ),
            'misc_blurb'    : self._remap_from( 'blurb' ),
            'file_ext'      : self._remap_from( 'extension' ),
            'file_path'     : self._remap_from( 'file_name' ),

            'resubmitted'   : lambda t, i, k: i._state == t.app.model.Dataset.states.RESUBMITTED,

            'display_apps'  : self.serialize_display_apps,
            'display_types' : self.serialize_old_display_applications,
            'visualizations': self.serialize_visualization_links,

            # 'url'   : url_for( 'history_content_typed', history_id=encoded_history_id, id=encoded_id, type="dataset" ),
            # TODO: this intermittently causes a routes.GenerationException - temp use the legacy route to prevent this
            #   see also: https://trello.com/c/5d6j4X5y
            #   see also: https://sentry.galaxyproject.org/galaxy/galaxy-main/group/20769/events/9352883/
            'url'           : lambda t, i, k: self.url_for( 'history_content',
                history_id=t.security.encode_id( i.history_id ), id=t.security.encode_id( i.id ) ),
            'urls'          : self.serialize_urls,

            # TODO: backwards compat: need to go away
            'download_url'  : lambda t, i, k: self.url_for( 'history_contents_display',
                history_id=t.security.encode_id( i.history.id ),
                history_content_id=t.security.encode_id( i.id ) ),
            'parent_id'     : self.serialize_id,
            'accessible'    : lambda *a: True,
            'api_type'      : lambda *a: 'file',
            'type'          : lambda *a: 'file'
        })

    def serialize_type_id( self, trans, hda, key ):
        return 'dataset-' + self.serializers[ 'id' ]( trans, hda, 'id' )

    def serialize_display_apps( self, trans, hda, key ):
        """
        Return dictionary containing new-style display app urls.
        """
        display_apps = []
        for display_app in hda.get_display_applications( trans ).itervalues():

            app_links = []
            for link_app in display_app.links.itervalues():
                app_links.append({
                    'target': link_app.url.get( 'target_frame', '_blank' ),
                    'href': link_app.get_display_url( hda, trans ),
                    'text': gettext.gettext( link_app.name )
                })
            if app_links:
                display_apps.append( dict( label=display_app.name, links=app_links ) )

        return display_apps

    def serialize_old_display_applications( self, trans, hda, key ):
        """
        Return dictionary containing old-style display app urls.
        """
        display_apps = []
        if not self.app.config.enable_old_display_applications:
            return display_apps

        display_link_fn = hda.datatype.get_display_links
        for display_app in hda.datatype.get_display_types():
            target_frame, display_links = display_link_fn( hda, display_app, self.app, trans.request.base )

            if len( display_links ) > 0:
                display_label = hda.datatype.get_display_label( display_app )

                app_links = []
                for display_name, display_link in display_links:
                    app_links.append({
                        'target': target_frame,
                        'href': display_link,
                        'text': gettext.gettext( display_name )
                    })
                if app_links:
                    display_apps.append( dict( label=display_label, links=app_links ) )

        return display_apps

    def serialize_visualization_links( self, trans, hda, key ):
        """
        Return a list of dictionaries with links to visualization pages
        for those visualizations that apply to this hda.
        """
        # use older system if registry is off in the config
        if not self.app.visualizations_registry:
            return hda.get_visualizations()
        return self.app.visualizations_registry.get_visualizations( trans, hda )

    def serialize_urls( self, trans, hda, key ):
        """
        Return web controller urls useful for this HDA.
        """
        url_for = self.url_for
        encoded_id = self.app.security.encode_id( hda.id )
        urls = {
            'purge'         : url_for( controller='dataset', action='purge_async', dataset_id=encoded_id ),
            'display'       : url_for( controller='dataset', action='display', dataset_id=encoded_id, preview=True ),
            'edit'          : url_for( controller='dataset', action='edit', dataset_id=encoded_id ),
            'download'      : url_for( controller='dataset', action='display',
                                       dataset_id=encoded_id, to_ext=hda.extension ),
            'report_error'  : url_for( controller='dataset', action='errors', id=encoded_id ),
            'rerun'         : url_for( controller='tool_runner', action='rerun', id=encoded_id ),
            'show_params'   : url_for( controller='dataset', action='show_params', dataset_id=encoded_id ),
            'visualization' : url_for( controller='visualization', action='index',
                                       id=encoded_id, model='HistoryDatasetAssociation' ),
            'meta_download' : url_for( controller='dataset', action='get_metadata_file',
                                       hda_id=encoded_id, metadata_name='' ),
        }
        return urls


class HDADeserializer( datasets.DatasetAssociationDeserializer,
                       taggable.TaggableDeserializerMixin,
                       annotatable.AnnotatableDeserializerMixin ):
    """
    Interface/service object for validating and deserializing dictionaries into histories.
    """
    model_manager_class = HDAManager

    def __init__( self, app ):
        super( HDADeserializer, self ).__init__( app )
        self.hda_manager = self.manager

    def add_deserializers( self ):
        super( HDADeserializer, self ).add_deserializers()
        taggable.TaggableDeserializerMixin.add_deserializers( self )
        annotatable.AnnotatableDeserializerMixin.add_deserializers( self )

        self.deserializers.update({
            'visible'       : self.deserialize_bool,
            # remapped
            'genome_build'  : lambda t, i, k, v: self.deserialize_genome_build( t, i, 'dbkey', v ),
            'misc_info'     : lambda t, i, k, v: self.deserialize_basestring( t, i, 'info', v ),
        })
        self.deserializable_keyset.update( self.deserializers.keys() )


class HDAFilterParser( datasets.DatasetAssociationFilterParser,
                       taggable.TaggableFilterMixin,
                       annotatable.AnnotatableFilterMixin ):
    model_class = model.HistoryDatasetAssociation

    def _add_parsers( self ):
        super( HDAFilterParser, self )._add_parsers()
        taggable.TaggableFilterMixin._add_parsers( self )
        annotatable.AnnotatableFilterMixin._add_parsers( self )
