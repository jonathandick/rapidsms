"""

WELTEL core logic

"""

import re
import rapidsms
import logging
from rapidsms.parsers.keyworder import Keyworder
from rapidsms.i18n import ugettext_noop as _
from reporters.models import Reporter, PersistantConnection
from scheduler.models import set_daily_event, EventSchedule
from weltel.formslogic import WeltelFormsLogic, REGISTER_COMMAND, NURSE_COMMAND
from weltel.models import Nurse, Patient, PatientState, OutcomeType, ProblemType, EventType

SAWA_CODE = 'sawa'
SHIDA_CODE = 'shida'
OTHER_CODE = 'other'
WELTEL_KEYWORD = "well"
PATIENT_ID_REGEX = "[a-z]+/[0-9]+"

class App (rapidsms.app.App):
    kw = Keyworder()
    bootstrapped = False
    
    def start (self):
        """Configure your app in the start phase."""
        if not self.bootstrapped:
            # initialize the forms app for registration
            self._form_app = self.router.get_app("form")
            # this tells the form app to add itself as a message handler
            # which registers the regex and function that this will dispatch to
            self._form_app.add_message_handler_to(self)
            
            formslogic = WeltelFormsLogic()
            formslogic.app = self
            # this tells the form app that this is also a form handler 
            self._form_app.add_form_handler("weltel", formslogic)
            
            self.boostrapped = True
            
            # we could also put this in the fixture
            # set up automatic deregistration event
            try:
                EventSchedule.objects.get(callback="weltel.callbacks.automatic_deregistration")
            except EventSchedule.DoesNotExist:
                # automatically deregister users we haven't seen in 3 weeks
                set_daily_event("weltel.callbacks.automatic_deregistration", \
                                hour=0, minute=15, callback_args=3)
                
            # set up bi-weekly shida report
            try:
                EventSchedule.objects.get(callback="weltel.callbacks.shida_report")
            except EventSchedule.DoesNotExist:
                schedule = EventSchedule(callback="weltel.callbacks.shida_report", \
                                         hours=set([8,15]), minutes=set([0]) )
                schedule.save()

    def parse (self, message):
        """Parse and annotate messages in the parse phase."""
        pass

    def handle (self, message):
        try:
            message.reporter = Patient.objects.get(reporter_ptr=message.reporter)
        except Patient.DoesNotExist:
            try:
                message.reporter = Nurse.objects.get(reporter_ptr=message.reporter)
            except Nurse.DoesNotExist:
                pass
        # use the keyworder to see if the forms app can help us
        try:
            if hasattr(self, "kw"):
                self.debug("WELTEL HANDLE")
                
                # attempt to match tokens in this message
                # using the keyworder parser
                results = self.kw.match(self, message.text)
                if results:
                    func, captures = results
                    # if a function was returned, then a this message
                    # matches the handler _func_. call it, and short-
                    # circuit further handler calls
                    func(self, message, *captures)
                    return True
                else:
                    self.other(message)
            else:
                self.debug("App does not instantiate Keyworder as 'kw'")
        except Exception:
            self.log_last_exception()
        
        # this is okay.  one of the other apps may yet pick it up
        self.info("Message doesn't match weltel. %s", message)    

    def cleanup (self, message):
        """Perform any clean up after all handlers have run in the
           cleanup phase."""
        pass

    def outgoing (self, message):
        """Handle outgoing message notifications."""
        pass

    def stop (self):
        """Perform global app cleanup when the application is stopped."""
        pass
    
    def add_message_handler(self, regex, function):
        '''Registers a message handler with this app.  
           Incoming messages that match this will call the function'''
        self.info("Registering regex: %s for function %s, %s" % \
                  (regex, function.im_class, function.im_func.func_name))
        self.kw.regexen.append((re.compile(regex, re.IGNORECASE), function))
    
    def is_patient(f):
        def decorator(self, message, *args):
            if not isinstance(message.reporter,Patient):
                message.respond( _("This number is not registered. ") + \
                                 REGISTER_COMMAND )
                return
            f(self, message, *args)
        return decorator
    
    def is_nurse(f):
        def decorator(self, message, *args):
            if not isinstance(message.reporter,Nurse):
                message.respond( _("This number is not registered to a nurse.") )
                return
            f(self, message, *args)
        return decorator
        
    def is_weltel_user(f):
        def decorator(self, message, *args):
            if not isinstance(message.reporter,Patient) and \
                not isinstance(message.reporter,Nurse):
                    message.respond( _("This number is not registered.") + \
                                     REGISTER_COMMAND )
                    return
            f(self, message, *args)
        return decorator        

    @kw("(%s)\s+(numbers)" % PATIENT_ID_REGEX)
    @is_nurse
    def outcome(self, message, patient_id, outcome_code):
        """ Expecting a text from a nurse of the form: <patient-id> <outcome-code> """
        try:
            patient = Patient.objects.get(alias=patient_id)
        except Patient.DoesNotExist:
            message.respond( _("Patient (%(id)s) not recognized.")%{'id':patient_id} )
            return
        try:
            outcome = patient.register_event(outcome_code, message.reporter)
        except OutcomeType.DoesNotExist:
            message.respond( _("Outcome (%(code)s) not recognized.")%{'code':outcome_code} )
            return
        patient.related_messages.add(message.persistent_msg)
        logging.info("Patient %s set to '%s'" % (message.reporter.alias, outcome_code))
        message.respond( _("Patient %(id)s updated to '%(code)s'") % \
                         {'id':patient_id, 'code':outcome.name} )

    # for unit tests ONLY - sends messages to all nurses!
    @kw("%(well)s report.*" % {'well':WELTEL_KEYWORD} )
    @is_nurse
    def shida_report(self, message):
        from weltel.callbacks import shida_report
        shida_report(self.router)

    @kw("(%s)\s*(.*)" % PATIENT_ID_REGEX)
    def from_other_phone(self, message, patient_id, text):
        message.reporter = Patient.objects.get(alias=patient_id)
        try:
            func,groups = self.kw.match(None, text)
        except TypeError:
            # no matches found
            self.other(message)
            return
        func(self, message, groups)

    @kw("(sawa|poa|nzuri|safi).*")
    @is_patient
    def sawa(self, message, sawa):
        message.reporter.register_event(SAWA_CODE)
        message.reporter.related_messages.add(message.persistent_msg)
        # Note that all messages are already logged in logger
        logging.info("Patient %s set to '%s'" % (message.reporter.alias, SAWA_CODE))
        message.respond( _("Asante") )
    
    @kw("shida\s*([0-9]+).*")
    @is_patient
    def shida(self, message, problem_code):
        response = ''
        try:
            problem = message.reporter.register_event(problem_code)
        except ProblemType.DoesNotExist:
            response = _("Problem %(code)s not recognized. ") % \
                        {'code':problem_code} 
            problem = message.reporter.register_event(SHIDA_CODE)
        message.reporter.related_messages.add(message.persistent_msg)
        logging.info("Patient %s set to '%s'" % (message.reporter.alias, SHIDA_CODE) )
        message.respond( response + _("Pole for '%(code)s'") % {'code':problem.name} )
    
    @kw("(shida)(whatever)?")
    @is_patient
    def shida_new(self, message, shida, new_problem=None):
        message.reporter.register_event(SHIDA_CODE)
        message.reporter.related_messages.add(message.persistent_msg)
        logging.info("Patient %s set to '%s'" % (message.reporter.alias, SHIDA_CODE) )
        message.respond( _("Pole") )

    @kw("(%(well)s set phone.*)" % {'well':WELTEL_KEYWORD} )
    @is_patient
    def set_default_phone(self, message, shida, new_problem=None):
        message.reporter.set_preferred_connection( message.persistant_connection )
        message.reporter.related_messages.add(message.persistent_msg)
        logging.info("Patient %(id)s default phone set to %(num)s" % \
                         {"num": message.persistant_connection.identity, \
                          "id":message.reporter.patient_id } )
        message.respond( "Patient %(id)s default phone set to %(num)s" % \
                         {"num": message.persistant_connection.identity, \
                          "id":message.reporter.patient_id } )
        
    # for unit tests
    @kw("(%(well)s get phone.*)" % {'well':WELTEL_KEYWORD} )
    @is_patient
    def get_default_phone(self, message, shida, new_problem=None):
        message.reporter.related_messages.add(message.persistent_msg)
        message.respond( "Patient %(id)s default phone is %(num)s" % \
                         {"num": message.reporter.connection.identity, \
                          "id": message.reporter.patient_id } )

    @kw("(%(well)s subscribe.*)" % {'well':WELTEL_KEYWORD} )
    @is_weltel_user
    def subscribe(self, message, shida, new_problem=None):
        message.reporter.subscribe()
        if isinstance(message.reporter,Patient):
            message.reporter.related_messages.add(message.persistent_msg)
        logging.info("Patient %s subscribed" % (message.reporter.alias) )
        message.respond( _("Karibu") )

    @kw("(%(well)s unsubscribe.*)" % {'well':WELTEL_KEYWORD} )
    @is_weltel_user
    def unsubscribe(self, message, shida, new_problem=None):
        message.reporter.unsubscribe()
        if isinstance(message.reporter,Patient):
            message.reporter.related_messages.add(message.persistent_msg)
        logging.info("%s unsubscribed" % (message.reporter.alias) )
        message.respond( _("Kwaheri") )

    @kw("%(well)s\s+phone\s+(%(id)s).*"  % {'well':WELTEL_KEYWORD, 'id':PATIENT_ID_REGEX} )
    def add_phone(self, message, patient_id):
        try:
            patient = Patient.objects.get(alias=patient_id)
        except Patient.DoesNotExist:
            message.respond(_("Unknown patient %(id)s.") % {'id':patient_id})
            return
        patient.related_messages.add(message.persistent_msg)
        conn, c_created = PersistantConnection.objects.get_or_create(\
                              identity=message.persistant_connection.identity, \
                              backend=message.persistant_connection.backend)
        if conn.reporter != patient:
            patient.connections.add(conn)
            message.respond( "Phone number %(num)s has been registered to patient %(id)s" % \
                             {"id":patient.patient_id, "num":conn.identity} )
        else:
            message.respond( "Phone number %(num)s is already registered with patient %(id)s" % \
                             {"id":patient.patient_id, "num":conn.identity} )

    @kw("%(well)s\s+phones.*" % {'well':WELTEL_KEYWORD} )
    @is_patient
    def list_phones(self, message):
        message.reporter.related_messages.add(message.persistent_msg)
        identities = [conn.identity for conn in message.reporter.connections.all()]
        response = ', '.join(identities)
        message.respond( response )

    @is_patient
    def other(self, message):
        message.reporter.register_event(OTHER_CODE)
        message.reporter.related_messages.add(message.persistent_msg)
        logging.info("Patient %s sent unrecognized command '%s'" % \
                     (message.reporter.alias, OTHER_CODE))
        message.respond( _("Command not recognized") )
