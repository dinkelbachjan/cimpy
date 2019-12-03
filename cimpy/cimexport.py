import os
import importlib
import chevron
from datetime import datetime
from enum import Enum
from time import time
import logging

logger = logging.getLogger(__name__)


# This function gets all attributes of an object and resolves references to other objects
def _get_class_attributes_with_references(res, version):
    class_attributes_list = []

    for key in res.keys():
        class_dict = dict(name=res[key].__class__.__name__)
        class_dict['mRID'] = key
        # array containing all attributes, attribute references to objects
        attributes_dict = _get_attributes(res[key])
        # change attribute references to mRID of the object, res needed because classes like SvPowerFlow does not have
        # mRID as an attribute. Therefore the corresponding class has to be searched in the res dictionary
        class_dict['attributes'] = _get_reference_uuid(attributes_dict, version, res, key)
        class_attributes_list.append(class_dict)
        del class_dict

    return class_attributes_list


# This function resolves references to objects
def _get_reference_uuid(attr_dict, version, res, mRID):
    reference_list = []
    base_class_name = 'cimpy.' + version + '.Base'
    base_module = importlib.import_module(base_class_name)
    base_class = getattr(base_module, 'Base')
    for key in attr_dict:
        if key in ['readInProfile', 'possibleProfileList']:
            reference_list.append({key: attr_dict[key]})
            continue

        attributes = {}
        if isinstance(attr_dict[key], list):  # many
            array = []
            for elem in attr_dict[key]:
                if issubclass(type(elem), base_class):
                    # classes like SvVoltage does not have an attribute called mRID, the mRID is only stored as a key
                    # for this object in the res dictionary
                    # The % added before the mRID is used in the lambda _set_attribute_or_reference
                    if not hasattr(elem, 'mRID'):
                        # search for the object in the res dictionary and return the mRID
                        UUID = '%' + _search_mRID(elem, res)
                        if UUID == '%':
                            logger.warning('Object of type {} not found as reference for object with UUID {}.'.format(
                                elem.__class__.__name__, mRID))
                    else:
                        UUID = '%' + elem.mRID

                    array.append(UUID)
                else:
                    logger.warning('Reference object not subclass of Base class for object with UUID {}.'.format(mRID))
            if len(array) == 1:
                attributes['value'] = array[0]
            else:
                attributes['value'] = array
        elif issubclass(type(attr_dict[key]), base_class):  # 0..1, 1..1
            # resource = key + ' rdf:resource='
            if not hasattr(attr_dict[key], 'mRID'):
                # search for object in res dict and return mRID
                # The % added before the mRID is used in the lambda _set_attribute_or_reference
                UUID = '%' + _search_mRID(attr_dict[key], res)
                if UUID == '%':
                    logger.warning('Object of type {} not found as reference for object with UUID {}.'.format(
                        elem.__class__.__name__, mRID))
            else:
                UUID = '%' + attr_dict[key].mRID
            attributes['value'] = UUID
        elif attr_dict[key] == "" or attr_dict[key] is None:
            pass
        else:
            attributes['value'] = attr_dict[key]

        attributes['attr_name'] = key
        if 'value' in attributes.keys():
            if isinstance(attributes['value'], list):
                for reference_item in attributes['value']:
                    # ignore default values
                    if reference_item not in ['', None, 0.0, 0]:
                        reference_list.append({'value': reference_item, 'attr_name': key})
            # ignore default values
            elif attributes['value'] not in ['', None, 0.0, 0, 'many']:
                reference_list.append(attributes)

    return reference_list


# This function searches a class_object in the res dictionary and returns the corresponding key (the mRID). Necessary
# for classes without mRID as attribute like SvVoltage
def _search_mRID(class_object, res):
    for mRID, class_obj in res.items():
        if class_object == class_obj:
            return mRID
    return ""


# Lambda function for chevron renderer to decide whether the current element is a reference or an attribute
def _set_attribute_or_reference(text, render):
    result = render(text)
    result = result.split('@')
    value = result[0]
    attr_name = result[1]
    if '%' in value:
        reference = value.split('%')[1]
        return ' rdf:resource="#' + reference + '"/>'
    else:
        return '>' + value + '</cim:' + attr_name + '>'


# Lambda function for chevron renderer to set an attribute or a reference in the model description.
def _set_attribute_or_reference_model(text, render):
    result = render(text)
    result = result.split('@')
    value = result[0]
    attr_name = result[1]
    if '%' in value:
        reference = value.split('%')[1]
        return ' rdf:resource="' + reference + '"/>'
    else:
        return '>' + value + '</md:Model.' + attr_name + '>'


# Restructures the namespaces dict into a list. The template engine writes each entry in the RDF header
def _create_namespaces_list(namespaces_dict):
    namespaces_list = []

    for key in namespaces_dict:
        namespace = dict(key=key, url=namespaces_dict[key])
        namespaces_list.append(namespace)

    return namespaces_list


# This function sorts the classes and their attributes to the corresponding profiles. Either the classes/attributes are
# imported or they are set afterwards. In the first case the readInProfile is used to determine from which profile this
# class/attribute was read. If an entry exists the class/attribute is added to this profile. In the
# possibleProfileList dictionary the possible origins of the class/attributes is stored. All profiles have a different
# priority which is stored in the enum cgmesProfile. As default the smallest entry in the dictionary is used to
# determine the profile for the class/attributes.
def _sort_classes_to_profile(class_attributes_list, activeProfileList):
    export_dict = {}
    export_about_dict = {}

    # iterate over classes
    for klass in class_attributes_list:
        same_package_list = []
        about_dict = {}

        # store readInProfile and possibleProfileList
        # readInProfile class attribute, same for multiple instances of same class, only last origin of variable stored
        # ToDo: check if multiple attribute origins are possible for read in attributes
        readInProfile = klass['attributes'][0]['readInProfile']
        possibleProfileList = klass['attributes'][1]['possibleProfileList']

        class_serializationProfile = ''

        if 'class' in readInProfile.keys():
            # class was imported
            if readInProfile['class'] in activeProfileList:
                # else: class origin profile not active for export, get active profile from possibleProfileList
                if readInProfile['class'] in possibleProfileList[klass['name']]['class']:
                    # profile active and in possibleProfileList
                    # else: class should not have been imported from this profile, get allowed profile
                    # from possibleProfileList
                    class_serializationProfile = readInProfile['class']
                else:
                    logger.warning('Class {} was read from profile {} but this profile is not possible for this class'
                                   .format(klass['name'], readInProfile['class']))
            else:
                logger.info('Class {} was read from profile {} but this profile is not active for the export. Use'
                            'default profile from possibleProfileList.'.format(klass['name'], readInProfile['class']))

        if class_serializationProfile == '':
            # class was created
            if klass['name'] in possibleProfileList.keys():
                if 'class' in possibleProfileList[klass['name']].keys():
                    possibleProfileList[klass['name']]['class'].sort()
                    for serializationProfile in possibleProfileList[klass['name']]['class']:
                        if cgmesProfile(serializationProfile).name in activeProfileList:
                            # active profile for class export found
                            class_serializationProfile = cgmesProfile(serializationProfile).name
                            break
                    if class_serializationProfile == '':
                        # no profile in possibleProfileList active
                        logger.warning('All possible export profiles for class {} not active. Skip class for export.'
                                       .format(klass['name']))
                        continue
                else:
                    logger.warning('Class {} has no profile to export to.'.format(klass['name']))
            else:
                logger.warning('Class {} has no profile to export to.'.format(klass['name']))

        # iterate over attributes
        for attribute in klass['attributes']:
            if 'attr_name' in attribute.keys():
                attribute_class = attribute['attr_name'].split('.')[0]
                attribute_name = attribute['attr_name'].split('.')[1]

                # IdentifiedObject.mRID is not exported as an attribute
                if attribute_name == 'mRID':
                    continue

                attribute_serializationProfile = ''

                if attribute_name in readInProfile.keys():
                    # attribute was imported
                    if readInProfile[attribute_name] in activeProfileList:
                        attribute_serializationProfile = readInProfile[attribute_name]
                    else:
                        logger.info('Attribute {} from class {} was read from profile {} but this profile is inactive'
                                    'for the export. Use default profile from possibleProfileList.'
                                    .format(attribute_name, klass['name'], readInProfile[attribute_name]))

                if attribute_serializationProfile == '':
                    # attribute was added
                    if attribute_class in possibleProfileList.keys():
                        if attribute_name in possibleProfileList[attribute_class].keys():
                            possibleProfileList[attribute_class][attribute_name].sort()
                            for serializationProfile in possibleProfileList[attribute_class][attribute_name]:
                                if cgmesProfile(serializationProfile).name in activeProfileList:
                                    # active profile for class export found
                                    attribute_serializationProfile = cgmesProfile(serializationProfile).name
                                    break
                            if attribute_serializationProfile == '':
                                # no profile in possibleProfileList active, skip attribute
                                logger.warning('All possible export profiles for attribute {}.{} of class {} '
                                               'not active. Skip attribute for export.'
                                               .format(attribute_class, attribute_name, klass['name']))
                                continue
                        else:
                            logger.warning('Attribute {}.{} of class {} has no profile to export to.'.
                                           format(attribute_class, attribute_name, klass['name']))
                    else:
                        logger.warning('The class {} for attribute {} is not in the possibleProfileList'.format(
                            attribute_class, attribute_name))

                if attribute_serializationProfile == class_serializationProfile:
                    # class and current attribute belong to same profile
                    same_package_list.append(attribute)
                else:
                    # class and current attribute does not belong to same profile -> rdf:about in
                    # attribute origin profile
                    if attribute_serializationProfile in about_dict.keys():
                        about_dict[attribute_serializationProfile].append(attribute)
                    else:
                        about_dict[attribute_serializationProfile] = [attribute]

        # add class with all attributes in the same profile to the export dict sorted by the profile
        if class_serializationProfile in export_dict.keys():
            export_class = dict(name=klass['name'], mRID=klass['mRID'], attributes=same_package_list)
            export_dict[class_serializationProfile]['classes'].append(export_class)
            del export_class
        else:
            export_class = dict(name=klass['name'], mRID=klass['mRID'], attributes=same_package_list)
            export_dict[class_serializationProfile] = {'classes': [export_class]}

        # add class with all attributes defined in another profile to the about_key sorted by the profile
        for about_key in about_dict.keys():
            if about_key in export_about_dict.keys():
                export_about_class = dict(name=klass['name'], mRID=klass['mRID'], attributes=about_dict[about_key])
                export_about_dict[about_key]['classes'].append(export_about_class)
            else:
                export_about_class = dict(name=klass['name'], mRID=klass['mRID'], attributes=about_dict[about_key])
                export_about_dict[about_key] = {'classes': [export_about_class]}

    return export_dict, export_about_dict


def cim_export(res, namespaces_dict, file_name, version, activeProfileList):
    """Function for serialization of cgmes classes

    This function serializes cgmes classes with the template engine chevron. The classes are separated by their profile
    and one xml file for each profile is created. The package name is added after the file name. The
    set_attributes_or_reference function is a lamda function for chevron to decide whether the value of an attribute is
    a reference to another class object or not.

    :param res: a dictionary containing the cgmes classes accessible via the mRID
    :param namespaces_dict: a dictionary containing the RDF namespaces used in the imported xml files
    :param file_name: a string with the name of the xml files which will be created
    :param version: cgmes version, e.g. version = "cgmes_v2_4_15"
    :param activeProfileList: a list containing the strings of all short names of the profiles used for serialization
    """

    cwd = os.getcwd()
    os.chdir(os.path.dirname(__file__))
    t0 = time()
    logger.info('Start export procedure.')

    # returns all classes with their attributes and resolved references
    class_attributes_list = _get_class_attributes_with_references(res, version)

    # determine class and attribute export profiles. The export dict contains all classes and their attributes where
    # the class definition and the attribute definitions are in the same profile. Every entry in about_dict generates
    # a rdf:about in another profile
    export_dict, about_dict = _sort_classes_to_profile(class_attributes_list, activeProfileList)

    namespaces_list = _create_namespaces_list(namespaces_dict)

    # get information for Model header
    created = {'attr_name': 'created', 'value': datetime.now().strftime("%d/%m/%Y %H:%M:%S")}
    authority = {'attr_name': 'modelingAuthoritySet', 'value': 'www.acs.eonerc.rwth-aachen.de'}

    # iterate over all profiles
    for profile_name, short_name in short_profile_name.items():
        model_name = {'mRID': file_name, 'description': []}
        model_description = {'model': [model_name]}
        model_description['model'][0]['description'].append(created)
        model_description['model'][0]['description'].append(authority)

        if short_name not in export_dict.keys() and short_name not in about_dict.keys():
            # nothing to do for current profile
            continue
        else:
            # extract class lists from export_dict and about_dict
            if short_name in export_dict.keys():
                classes = export_dict[short_name]['classes']
            else:
                classes = False

            if short_name in about_dict.keys():
                about = about_dict[short_name]['classes']
            else:
                about = False

        # File name
        full_file_name = file_name + '_' + profile_name + '.xml'

        full_path = os.path.join(cwd, full_file_name)

        profile = {'attr_name': 'profile', 'value': profile_name}
        model_description['model'][0]['description'].append(profile)

        if not os.path.exists(full_path):
            with open(full_path, 'w') as file:
                logger.info('Write file \"%s\"', full_path)

                with open('export_template.mustache') as f:
                    output = chevron.render(f, {"classes": classes,
                                                "about": about,
                                                "set_attributes_or_reference": _set_attribute_or_reference,
                                                "set_attributes_or_reference_model": _set_attribute_or_reference_model,
                                                "namespaces": namespaces_list,
                                                "model": model_description['model']})
                file.write(output)
        else:
            logger.warning('File {} already exists in path {}. Delete file or change file name to serialize CGMES '
                           'classes.'.format(full_file_name, cwd))
        del model_description, model_name
    os.chdir(cwd)
    logger.info('End export procedure. Elapsed time: {}'.format(time() - t0))


# This function extracts all attributes from class_object in the form of Class_Name.Attribute_Name
def _get_attributes(class_object):
    inheritance_list = [class_object]
    class_type = type(class_object)
    parent = class_object

    # get parent classes
    while 'Base.Base' not in str(class_type):
        parent = parent.__class__.__bases__[0]()
        # insert parent class at beginning of list, classes inherit from top to bottom
        inheritance_list.insert(0, parent)
        class_type = type(parent)

    # dictionary containing all attributes with key: 'Class_Name.Attribute_Name'
    attributes_dict = dict(readInProfile=class_object.readInProfile, possibleProfileList={})

    # __dict__ of a subclass returns also the attributes of the parent classes
    # to avoid multiple attributes create list with all attributes already processed
    attributes_list = []

    # iterate over parent classes from top to bottom
    for parent_class in inheritance_list:
        # get all attributes of the current parent class
        parent_attributes_dict = parent_class.__dict__
        class_name = parent_class.__class__.__name__

        # check if new attribute or old attribute
        for key in parent_attributes_dict.keys():
            if key not in attributes_list:
                attributes_list.append(key)
                attributes_name = class_name + '.' + key
                attributes_dict[attributes_name] = getattr(class_object, key)
            else:
                continue

        # get all possibleProfileLists from all parent classes except the Base class (no attributes)
        # the readInProfile from parent classes is not needed because entries in the readInProfile are only generated
        # for the inherited class
        if class_name is not 'Base':
            attributes_dict['possibleProfileList'][class_name] = parent_class.possibleProfileList

    return attributes_dict


# Mapping between the profiles and their short names
short_profile_name = {
    "DiagramLayout": 'DI',
    "Dynamics": "DY",
    "Equipment": "EQ",
    "GeographicalLocation": "GL",
    "StateVariables": "SV",
    "SteadyStateHypothesis": "SSH",
    "Topology": "TP"
}

# Enum containing all profiles and their export priority
cgmesProfile = Enum("cgmesProfile", {"EQ": 0, "SSH": 1, "TP": 2, "SV": 3, "DY": 4, "GL": 5, "DI": 6})
